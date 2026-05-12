#!/usr/bin/env python3
"""Print OCODE color swatches in your terminal so you can pick one by eye."""
from rich.console import Console

c = Console()
swatches = [
    ("#00ff00", "pure CGA green"),
    ("#39ff14", "neon green"),
    ("#00ff41", "matrix green"),
    ("#33ff33", "phosphor green"),
    ("#0fff50", "toxic green"),
    ("#7fff00", "chartreuse"),
    ("#a8ff00", "current (lime/yellow)"),
    ("#bfff00", "lime CSS"),
    ("#32cd32", "limegreen CSS"),
    ("#9aff00", "acid lime"),
]
for hex_, name in swatches:
    c.print(
        f"[bold {hex_}]████  ▰▰ OCODE  ▌  thinking…  → 234 tok · 27.9 tok/s[/]"
        f"   [dim]{hex_}  {name}[/]"
    )
