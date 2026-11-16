"""Task-specific instructions. Every mode works with any model authorized by SIWC."""
from textwrap import dedent

PRESETS = {
    "general": "Answer the user's request precisely. State uncertainties. Preserve useful structure and language.",
    "analysis": "Analyze assumptions, evidence, tradeoffs, failure modes, and a recommendation. Distinguish facts from estimates.",
    "research": "Provide a research synthesis. Use ONLY sources supplied in the input; do not pretend to have browsed. Identify gaps requiring verification, give source labels when provided.",
    "writing": "Write publication-quality original prose. Honor requested language, voice, audience, and length. Never invent citations, studies or quotations. Output content first, with minimal meta commentary.",
    "review": "Review the supplied material for defects, missing requirements, risks and actionable improvements. Prioritize severity; include concrete fixes.",
    "code": "Act as a senior software engineer. Provide correct, testable, maintainable code and design with explicit assumptions, concise explanations and edge cases.",
}


def article_prompt(*, topic: str, audience: str = "一般読者", tone: str = "自然で明快", approx_chars: int = 3000,
                   keywords: str = "", sources: str = "", language: str = "日本語") -> str:
    if not 500 <= approx_chars <= 15000:
        raise ValueError("approx_chars must be between 500 and 15000")
    return dedent(f"""\
        {language}でオリジナルのブログ記事を執筆してください。
        テーマ: {topic}
        対象読者: {audience}
        文体: {tone}
        分量の目安: 約{approx_chars}文字
        SEOキーワード: {keywords or '指定なし'}

        出力: タイトル案、メタディスクリプション、リード、H2/H3構造の本文、まとめ。
        キーワードを不自然に連呼しないでください。検索順位や成果を保証しないでください。
        実在しない研究・統計・引用・URLは創作せず、要検証の主張はその旨を示してください。
        参考資料（提供されている場合のみ使用）:
        {sources or '参考資料なし。最新情報を検索済みであるかのように振る舞わないこと。'}
    """)
