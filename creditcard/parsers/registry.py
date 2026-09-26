"""Maps the `parser` column in cards.csv to a parser instance.

Adding an issuer is one import plus one dict entry at the bottom of this file.
The dict is empty until the issuer parsers exist (Tasks 16-19); until then
get_parser raises with a message that names what was asked for and what is
available, which is exactly what a first run needs to say.
"""
from creditcard.parsers.base import Parser
from creditcard.parsers.hdfc import hdfc_parser
from creditcard.parsers.icici import icici_parser
from creditcard.parsers.sbi import sbi_parser


class UnknownParserError(Exception):
    pass


PARSERS: dict[str, Parser] = {
    "hdfc": hdfc_parser,
    "icici": icici_parser,
    "sbi": sbi_parser,
}


def get_parser(name: str) -> Parser:
    if name not in PARSERS:
        available = ", ".join(sorted(PARSERS)) or "(none registered)"
        raise UnknownParserError(f"No parser named {name!r}. Available: {available}")
    return PARSERS[name]
