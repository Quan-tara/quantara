import discord
from discord.ext import commands
from db.database import SessionLocal
from db.models import Contract, ContractSeries, Position, Order, PublishedRate
from engine.users import get_or_create_user, cancel_position as engine_cancel_position
from engine.orderbook import place_order, place_secondary_order, get_order_book, get_market_snapshot
from engine.execution import cancel_order, get_trades
from engine.settlement import settle_contract
from engine.wallet import get_wallet
from engine.index_provider import get_risk_index, get_index_snapshot, get_current_published_rate, get_running_estimate
from engine.pnl import get_user_pnl, get_positions_with_pnl, get_mm_pnl, calc_mark_price, calc_pnl
from engine.constants import MM_USER_ID, ADMIN_ID
from config import DISCORD_TOKEN
from datetime import datetime, timedelta
import math

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

def is_admin(ctx): return ctx.author.id == ADMIN_ID

def time_factor(hours: float, vol: float) -> float:
    return 1 + min(vol, 8) / 10 * math.sqrt(max(hours, 0) / 24)

def resolve_position_id(user_id, short):
    session = SessionLocal()
    try:
        for p in session.query(Position).filter(
            Position.user_id == user_id, Position.status == "OPEN").all():
            if p.id.startswith(short):
                return p.id
        return None
    finally:
        session.close()


@bot.event
async def on_ready():
    print(f"✅ Bot online as {bot.user}")
    get_or_create_user(MM_USER_ID)


@bot.command(name="help")
async def help_command(ctx):
    await ctx.send("""
📖 **QUANTARA — COMMANDS**

**Account**
`!balance` — wallet balance
`!positions` — open positions with live PnL
`!pnl` — detailed PnL

**Market Info**
`!contracts` — grid of all 12 series with live prices
`!book <series_id>` — order book for a series (1-12)
`!index` — current risk index + factors
`!rates` — published daily late delivery rates
`!trades [contract_id]` — recent trades

**Trading**
`!buy <series_id> <price> <qty>` — buy protection (HOLDER)
`!sell <series_id> <price> <qty>` — sell protection (WRITER)
`!cancel <order_id>` — cancel an order
`!offer <short_id> <price> <qty>` — list position for resale
`!buy_position <contract_id> <price> <qty>` — buy from secondary market
`!cancel_position <short_id>` — close position

*Admin: !launch <series_id>, !launch_all, !quote_all <spread%>, !settle <contract_id> YES|NO, !mm_pnl*
""")


@bot.command()
async def balance(ctx):
    session = SessionLocal()
    try:
        # If the MM/admin account calls !balance, show the MM (system) wallet
        uid = MM_USER_ID if ctx.author.id == ADMIN_ID else ctx.author.id
        get_or_create_user(uid)
        w = get_wallet(session, uid)
        label = "MM (System)" if uid == MM_USER_ID else ctx.author.name
        await ctx.send(
            f"💰 **{label}**\n"
            f"Cash: {w.cash_balance:.2f} | Locked: {w.locked_balance:.2f} | Available: {w.cash_balance-w.locked_balance:.2f}"
        )
    finally:
        session.close()


@bot.command()
async def positions(ctx):
    uid = MM_USER_ID if ctx.author.id == ADMIN_ID else ctx.author.id
    get_or_create_user(uid)
    await ctx.send(get_positions_with_pnl(uid))


@bot.command()
async def pnl(ctx):
    get_or_create_user(ctx.author.id)
    await ctx.send(get_user_pnl(ctx.author.id))


@bot.command()
async def index(ctx):
    snap = get_index_snapshot()
    idx  = snap["index"]
    status = "🟢 LOW" if idx < 20 else "🟡 ELEVATED" if idx < 50 else "🔴 HIGH"
    pub  = get_current_published_rate()
    lines = ""
    for f in snap["factors"]:
        bar  = "█" * int(f["value"] / 10) + "░" * (10 - int(f["value"] / 10))
        spk  = " ⚡" if f["spiking"] else ""
        lines += f"  {f['name'][:18]:<18} {bar} {f['value']:5.1f} [{f['weight_pct']}%]{spk}\n"
    await ctx.send(
        f"📊 **DELIVERY RISK INDEX: {idx:.2f}** — {status}\n"
        f"Latest published rate: **{pub['rate']:.1f}%** "
        f"({'🔴 IN MONEY' if pub['in_the_money'] else '🟢 OUT OF MONEY'} vs 20% threshold)\n"
        f"Running estimate today: **{get_running_estimate():.1f}%**\n"
        f"```\n{lines}```"
    )


@bot.command()
async def rates(ctx):
    session = SessionLocal()
    try:
        recent = session.query(PublishedRate).order_by(
            PublishedRate.period_end.desc()).limit(14).all()
        if not recent:
            await ctx.send("📭 No published rates yet")
            return
        msg = "📊 **PUBLISHED DAILY RATES** (threshold: 20%)\n```\n"
        msg += f"{'Date':<12} {'Rate':>7} {'Result':>8}\n"
        msg += "─" * 30 + "\n"
        for r in reversed(recent):
            date_str = r.period_end.strftime("%Y-%m-%d")
            result   = "YES ✅" if r.rate > 20.0 else "NO  ❌"
            msg     += f"{date_str:<12} {r.rate:>6.1f}% {result:>8}\n"
        msg += "```"
        await ctx.send(msg)
    finally:
        session.close()


@bot.command()
async def contracts(ctx):
    """Show the 12-series grid with live prices."""
    session = SessionLocal()
    try:
        snap = get_index_snapshot()
        idx  = snap["index"]; vol = snap["volatility"]
        pub  = get_current_published_rate()

        msg = f"📋 **QUANTARA CONTRACT GRID** — Index: {idx:.2f} | Rate: {pub['rate']:.1f}%\n```\n"
        msg += f"{'Series':<4} {'Label':<22} {'Mid':>8} {'Active':>8} {'Expires':>10}\n"
        msg += "─" * 58 + "\n"

        all_series = session.query(ContractSeries).order_by(
            ContractSeries.expiry_mins, ContractSeries.collateral).all()

        for s in all_series:
            active = session.query(Contract).filter(
                Contract.series_id == s.id, Contract.status == "OPEN").first()
            hrs = s.expiry_mins / 60
            tf  = time_factor(hrs, vol)
            mid = s.collateral * (idx / 100) * tf
            has = "YES" if active else " NO"
            exp = ""
            if active and active.expires_at:
                secs = int((active.expires_at - datetime.utcnow()).total_seconds())
                if secs > 0:
                    h, r = divmod(secs, 3600); m, _ = divmod(r, 60)
                    exp = f"{h}h{m}m" if h else f"{m}m"
            msg += f"{s.id:<4} {s.label:<22} {mid:>7.2f}  {has:>6}  {exp:>10}\n"

        msg += f"```\nUse `!buy <1-12> <price> <qty>` to trade"
        await ctx.send(msg)
    finally:
        session.close()


@bot.command()
async def book(ctx, series_id: int):
    session = SessionLocal()
    try:
        active = session.query(Contract).filter(
            Contract.series_id == series_id, Contract.status == "OPEN").first()
        if not active:
            await ctx.send(f"📭 No active contract for series {series_id}. Use `!launch {series_id}` (admin).")
            return
        data = get_order_book(active.id)
        bids = "\n".join([f"  {b['price']:.2f} x {b['qty']:.2f}{'  [MM]' if b['is_mm'] else ''}"
                          for b in data["bids"]]) or "  (empty)"
        asks = "\n".join([f"  {a['price']:.2f} x {a['qty']:.2f}{'  [MM]' if a['is_mm'] else ''}"
                          for a in data["asks"]]) or "  (empty)"
        s = session.query(ContractSeries).filter(ContractSeries.id == series_id).first()
        await ctx.send(
            f"📊 **{s.label}** (Contract #{active.id})\n"
            f"🟢 BIDS:\n{bids}\n\n🔴 ASKS:\n{asks}"
        )
    finally:
        session.close()


@bot.command()
async def trades(ctx, contract_id: int = None):
    tl = get_trades(contract_id=contract_id, limit=10)
    if not tl:
        await ctx.send("📭 No trades yet"); return
    msg = "📜 **RECENT TRADES**\n"
    for t in tl:
        msg += f"  {t['quantity']}@ {t['price']:.2f} | {t['trade_type']}\n"
    await ctx.send(msg)


@bot.command()
async def buy(ctx, series_id: int, price: float, quantity: float):
    """Buy protection on a series. Usage: !buy <series_id 1-12> <price> <qty>"""
    get_or_create_user(ctx.author.id)
    session = SessionLocal()
    try:
        active = session.query(Contract).filter(
            Contract.series_id == series_id, Contract.status == "OPEN").first()
        if not active:
            await ctx.send(f"❌ No active contract for series {series_id}")
            return
        contract_id = active.id
    finally:
        session.close()
    result = place_order(ctx.author.id, contract_id, "BUY", price, quantity)
    if isinstance(result, dict):
        await ctx.send(f"🟢 BUY order #{result['id']} on Series {series_id} @ {price} x {quantity}")
    else:
        await ctx.send(str(result))


@bot.command()
async def sell(ctx, series_id: int, price: float, quantity: float):
    """Sell protection on a series. Usage: !sell <series_id 1-12> <price> <qty>"""
    get_or_create_user(ctx.author.id)
    session = SessionLocal()
    try:
        active = session.query(Contract).filter(
            Contract.series_id == series_id, Contract.status == "OPEN").first()
        if not active:
            await ctx.send(f"❌ No active contract for series {series_id}")
            return
        contract_id = active.id
    finally:
        session.close()
    result = place_order(ctx.author.id, contract_id, "SELL", price, quantity)
    if isinstance(result, dict):
        await ctx.send(f"🔴 SELL order #{result['id']} on Series {series_id} @ {price} x {quantity}")
    else:
        await ctx.send(str(result))


@bot.command()
async def cancel(ctx, order_id: int):
    session = SessionLocal()
    try:
        await ctx.send(f"🗑️ {cancel_order(session, ctx.author.id, order_id)}")
    finally:
        session.close()


@bot.command()
async def myorders(ctx):
    """Show your open orders with their IDs so you know what to cancel."""
    uid = MM_USER_ID if ctx.author.id == ADMIN_ID else ctx.author.id
    session = SessionLocal()
    try:
        orders = session.query(Order).filter(
            Order.user_id == uid,
            Order.status  == "OPEN"
        ).order_by(Order.id.desc()).limit(10).all()
        if not orders:
            await ctx.send("📋 No open orders."); return
        lines = ["📋 **Your open orders:**"]
        for o in orders:
            side = "BUY" if o.side == "BUY" else "SELL"
            lines.append(f"  ID `{o.id}` — {side} Contract #{o.contract_id} @ {o.price:.2f} x {o.quantity} [{o.order_type}]")
        await ctx.send("\n".join(lines))
    finally:
        session.close()


@bot.command()
async def offer(ctx, short_position_id: str, price: float, quantity: float):
    full_id = resolve_position_id(ctx.author.id, short_position_id)
    if not full_id:
        await ctx.send(f"❌ Position `{short_position_id}` not found"); return
    session = SessionLocal()
    try:
        pos = session.query(Position).filter(Position.id == full_id).first()
        contract_id = pos.contract_id
    finally:
        session.close()
    result = place_secondary_order(ctx.author.id, contract_id, "SELL", price, quantity, full_id)
    if isinstance(result, dict):
        await ctx.send(f"🏷️ Listed @ {price:.2f}. Others: `!buy_position {contract_id} {price} {quantity}`")
    else:
        await ctx.send(str(result))


@bot.command()
async def buy_position(ctx, contract_id: int, price: float, quantity: float):
    get_or_create_user(ctx.author.id)
    result = place_secondary_order(ctx.author.id, contract_id, "BUY", price, quantity)
    if isinstance(result, dict):
        await ctx.send(f"🟢 Secondary BUY #{result['id']}")
    else:
        await ctx.send(str(result))


@bot.command()
async def cancel_position(ctx, short_id: str):
    uid = MM_USER_ID if ctx.author.id == ADMIN_ID else ctx.author.id
    result = engine_cancel_position(uid, short_id)
    await ctx.send(result)


# ── ADMIN ──

@bot.command()
async def launch(ctx, series_id: int):
    """[ADMIN] Launch a new contract for a series."""
    if not is_admin(ctx): await ctx.send("❌ Not authorized"); return
    session = SessionLocal()
    try:
        existing = session.query(Contract).filter(
            Contract.series_id == series_id, Contract.status == "OPEN").first()
        if existing:
            await ctx.send(f"❌ Series {series_id} already has open contract #{existing.id}")
            return
        s = session.query(ContractSeries).filter(ContractSeries.id == series_id).first()
        if not s:
            await ctx.send(f"❌ Series {series_id} not found"); return
        idx     = get_risk_index()
        premium = round(s.collateral * (idx / 100), 2)
        c = Contract(
            name=s.label, collateral=s.collateral, premium=premium,
            series_id=series_id, settlement_threshold=s.threshold, auto_settle=True,
            expires_at=datetime.utcnow() + timedelta(minutes=s.expiry_mins),
        )
        session.add(c)
        session.commit()
        session.refresh(c)
        await ctx.send(
            f"🚀 **Contract #{c.id} launched** — {s.label}\n"
            f"Premium: {premium:.2f} | Expires: {c.expires_at.strftime('%H:%M UTC')}"
        )
    except Exception as e:
        session.rollback()
        await ctx.send(f"❌ {e}")
    finally:
        session.close()


@bot.command()
async def launch_all(ctx):
    """[ADMIN] Launch all series that don't have an active contract."""
    if not is_admin(ctx): await ctx.send("❌ Not authorized"); return
    session = SessionLocal()
    launched = []
    try:
        snap = get_index_snapshot()
        idx  = snap["index"]; vol = snap["volatility"]
        for s in session.query(ContractSeries).all():
            existing = session.query(Contract).filter(
                Contract.series_id == s.id, Contract.status == "OPEN").first()
            if existing:
                continue
            hrs = s.expiry_mins / 60
            tf  = time_factor(hrs, vol)
            premium = round(s.collateral * (idx / 100) * tf, 2)
            c = Contract(
                name=s.label, collateral=s.collateral, premium=premium,
                series_id=s.id, settlement_threshold=s.threshold, auto_settle=True,
                expires_at=datetime.utcnow() + timedelta(minutes=s.expiry_mins),
            )
            session.add(c)
            launched.append(s.label)
        session.commit()
        await ctx.send(f"🚀 Launched {len(launched)} series:\n" + "\n".join(f"  • {l}" for l in launched))
    except Exception as e:
        session.rollback()
        await ctx.send(f"❌ {e}")
    finally:
        session.close()


@bot.command()
async def quote_all(ctx, spread_pct: float = 10.0):
    """[ADMIN] Post quotes on all active series. Usage: !quote_all [spread%]"""
    if not is_admin(ctx): await ctx.send("❌ Not authorized"); return
    session = SessionLocal()
    count = 0
    try:
        snap = get_index_snapshot()
        idx  = snap["index"]; vol = snap["volatility"]
        for s in session.query(ContractSeries).all():
            active = session.query(Contract).filter(
                Contract.series_id == s.id, Contract.status == "OPEN").first()
            if not active: continue
            hrs_left = max(0, (active.expires_at - datetime.utcnow()).total_seconds() / 3600)
            tf   = time_factor(hrs_left, vol)
            mid  = s.collateral * (idx / 100) * tf
            spread = spread_pct / 100
            bid  = round(mid * (1 - spread), 2)
            ask  = round(mid * (1 + spread), 2)
            place_order(MM_USER_ID, active.id, "BUY",  bid, 5.0)
            place_order(MM_USER_ID, active.id, "SELL", ask, 5.0)
            count += 1
    finally:
        session.close()
    await ctx.send(f"📡 Quoted {count} series at {spread_pct:.0f}% spread")


@bot.command()
async def settle(ctx, contract_id: int, result: str):
    """[ADMIN] Manually settle a contract."""
    if not is_admin(ctx): await ctx.send("❌ Not authorized"); return
    msg = settle_contract(contract_id, result)
    await ctx.send(msg)


@bot.command()
async def mm_pnl(ctx):
    if not is_admin(ctx): await ctx.send("❌ Not authorized"); return
    d = get_mm_pnl()
    await ctx.send(
        f"📊 **MM PnL**\n"
        f"Spread: {d['spread_pnl']:+.2f} | Received: {d['total_received']:.2f} | "
        f"Paid: {d['total_paid']:.2f} | Wallet: {d['wallet_balance']:.2f}"
    )


# Bot is started by web/app.py startup event via asyncio.create_task(bot.start(token))
# When running standalone locally: python bot/bot.py
if __name__ == "__main__":
    from config import DISCORD_TOKEN
    bot.run(DISCORD_TOKEN)
