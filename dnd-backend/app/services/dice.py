"""Броски кубиков. Выполняются только на сервере (криптографический ГСЧ),
чтобы результат нельзя было подделать на клиенте."""
import secrets


def roll(count: int, sides: int, modifier: int = 0) -> dict:
    rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
    return {
        "notation": f"{count}d{sides}{modifier:+d}" if modifier else f"{count}d{sides}",
        "rolls": rolls,
        "modifier": modifier,
        "total": sum(rolls) + modifier,
    }
