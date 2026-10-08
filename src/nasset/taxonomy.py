from __future__ import annotations

CASHFLOW_TAXONOMY = [
    {
        "id": "contractual",
        "level": 1,
        "name": "Contractual Income",
        "risk_label": "Defensive / contractual",
        "description": (
            "Cashflow is primarily defined by contract. Main risks are duration, "
            "credit quality, reinvestment and FX rather than operating execution."
        ),
        "subcategories": [
            {"id": "cash_short_rates", "name": "Cash & Short Rates", "examples": ["T-bills", "money-market funds"]},
            {"id": "sovereign_bonds", "name": "Sovereign Bonds", "examples": ["US Treasuries", "China government bonds"]},
            {"id": "investment_grade_credit", "name": "Investment Grade Credit", "examples": ["AAA / AA / A / BBB corporate bonds"]},
            {"id": "structured_senior_credit", "name": "Senior Structured Credit", "examples": ["AAA CLO"]},
        ],
    },
    {
        "id": "productive",
        "level": 2,
        "name": "Productive Distribution",
        "risk_label": "Operating / distribution",
        "description": (
            "Cashflow comes from productive businesses or listed real assets. "
            "Income depends on earnings, asset quality, leverage and payout policy."
        ),
        "subcategories": [
            {"id": "dividend_equity", "name": "Dividend Equity & Banks", "examples": ["China banks", "dividend stocks"]},
            {"id": "preferred_hybrid", "name": "Preferred & Hybrid", "examples": ["preferred stock", "AT1 / hybrids"]},
            {"id": "reit", "name": "Listed REITs", "examples": ["US REIT", "S-REIT", "J-REIT", "China REITs"]},
            {"id": "private_credit", "name": "Listed Private Credit", "examples": ["BDC"]},
            {"id": "infrastructure", "name": "Infrastructure & Utilities", "examples": ["pipelines", "utilities", "listed infrastructure"]},
        ],
    },
    {
        "id": "real_assets",
        "level": 3,
        "name": "Illiquid Real Assets",
        "risk_label": "Illiquidity / local operations",
        "description": (
            "Cashflow is tied to physical assets and local operating conditions. "
            "Valuation is slower, transaction costs are high and liquidity can disappear."
        ),
        "subcategories": [
            {"id": "direct_property", "name": "Direct Property", "examples": ["Chongli ski property", "rental housing"]},
            {"id": "farmland", "name": "Farmland", "examples": ["land rent", "crop-linked income"]},
            {"id": "timberland", "name": "Timberland", "examples": ["timber harvest"]},
            {"id": "royalties", "name": "Royalties", "examples": ["mineral", "energy", "IP royalties"]},
            {"id": "private_real_assets", "name": "Private Real Assets", "examples": ["private infrastructure", "private real estate"]},
        ],
    },
    {
        "id": "engineered",
        "level": 4,
        "name": "Engineered Yield",
        "risk_label": "Active / convexity / platform",
        "description": (
            "Cashflow is manufactured by selling optionality, locking capital or taking "
            "protocol / counterparty risk. Yield can be high because risk is explicitly sold."
        ),
        "subcategories": [
            {"id": "option_overlay", "name": "Option Overlay", "examples": ["covered call", "cash-secured put"]},
            {"id": "staking", "name": "Staking", "examples": ["ETH staking"]},
            {"id": "lending_carry", "name": "Lending & Carry", "examples": ["stablecoin lending", "securities lending"]},
            {"id": "basis_relative_value", "name": "Basis & Relative Value", "examples": ["cash-and-carry", "funding basis"]},
        ],
    },
]


def taxonomy_index() -> dict[tuple[str, str], dict]:
    result: dict[tuple[str, str], dict] = {}
    for layer in CASHFLOW_TAXONOMY:
        for subcategory in layer["subcategories"]:
            result[(layer["id"], subcategory["id"])] = {
                "layer": layer,
                "subcategory": subcategory,
            }
    return result
