"""Preserve source titles and prepare their math fragments for display and export."""
import html
import re

LETTERS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵"))
SYMBOLS = {"Rightarrow":"⇒", "rightarrow":"→", "leftarrow":"←", "Delta":"Δ", "delta":"δ",
           "alpha":"α", "beta":"β", "gamma":"γ", "epsilon":"ε", "eta":"η", "theta":"θ",
           "lambda":"λ", "mu":"μ", "sigma":"σ", "pi":"π", "neq":"≠", "leq":"≤", "geq":"≥",
           "times":"×", "star":"⋆", "infty":"∞", "ell":"ℓ"}
SUPER = str.maketrans("0123456789+-=()in", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ")
SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
MATH = re.compile(r"\$(?!\s)([^$\n]+?)\$|\\\((.+?)\\\)|"
                  r"(\\(?:mathcal|mathbb|mathrm|mathbf|text)(?:\{[^{}]+\}|[A-Z])(?:\^(?:\{\w+\}|\d))?|"
                  r"(?:\^)?\\(?:" + "|".join(SYMBOLS) + r")(?![a-zA-Z]))")


def readable_math(tex):
    text = re.sub(r"\\mathcal\{([A-Z])\}", lambda m: LETTERS[m[1]], tex)
    text = re.sub(r"\\(?:mathrm|mathbf|text)\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"\\([A-Za-z]+)", lambda m: SYMBOLS.get(m[1], m[0]), text)
    text = re.sub(r"\^\{?([0-9in+-]+)\}?", lambda m:m[1].translate(SUPER), text)
    text = re.sub(r"_\{?([0-9+-]+)\}?", lambda m:m[1].translate(SUB), text)
    return text


def prepare_title(paper):
    title = html.unescape(paper.get("title_official") or paper["title"])
    parts = []
    offset = 0
    for m in MATH.finditer(title):
        if m.start() > offset:
            parts.append({"text":title[offset:m.start()]})
        tex = next(group for group in m.groups() if group is not None)
        tex = re.sub(r"\\(mathcal|mathbf|mathbb|mathrm)([A-Z])", r"\\\1{\2}", tex)
        parts.append({"math":tex, "text":readable_math(tex)})
        offset = m.end()
    if offset < len(title):
        parts.append({"text":title[offset:]})
    paper["title_text"] = "".join(part["text"] for part in parts)
    if any("math" in part for part in parts):
        paper["title_parts"] = parts
    else:
        paper.pop("title_parts", None)
