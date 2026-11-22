import re
import unicodedata

LEGAL_FORMS = {"ооо", "ао", "пао", "ип", "зао", "oao", "llc", "ltd"}


def supplier_key(name: str) -> str:
    words = re.findall(r"\w+", unicodedata.normalize("NFKC", name).casefold())
    return " ".join(word for word in words if word not in LEGAL_FORMS) or " ".join(words) or name.casefold()
