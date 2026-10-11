"""Small, verified terminology hints; never a source of extra story context."""

import re


# Names checked in the game's Chinese localization; Revachol on its Steam page.
DISCO_TERMS = (
    ("Joyce Messier", "乔伊斯·梅西耶"),
    ("Lilienne", "莉莉恩"),
    ("Net Picker", "拾网人"),
    ("nautical knot", "海员结"),
    ("jettady-jetty", "栈桥；译文保留原文口吃"),
    ("Revachol", "瑞瓦肖"),
)


def game_hint(game, text):
    if game != "Disco Elysium":
        return ""
    hint = "\n当前文本来自《极乐迪斯科》。保留旁白、角色或技能的说话口吻、讽刺、粗口和有意的口吃。只处理提供的文本，不引入人物背景、任务信息、剧透或未出现的术语。"
    terms = []
    for english, chinese in DISCO_TERMS:
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in english.split()) + r"(?!\w)"
        if english == "jettady-jetty":
            # OCR inserts spaces inside this deliberately stuttered game word.
            pattern = r"(?<!\w)j\s*ettady\s*-\s*j\s*etty(?!\w)"
        if re.search(pattern, text, re.IGNORECASE):
            terms.append(f"{english} → {chinese}")
    if terms:
        hint += "\n当前文本中已核实的译名：" + "；".join(terms) + "。仅在对应英文出现时使用，不列出译名表。"
    return hint
