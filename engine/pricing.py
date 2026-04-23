from engine.index_provider import get_risk_index


def get_mark_price():
    """
    Single source of truth:
    Converts risk index → tradable asset price
    """

    index = get_risk_index()

    # Simple linear mapping (you can evolve later)
    return 10 + (index * 10)


def get_index():
    """
    Exposes raw index for debugging / UI
    """
    return get_risk_index()