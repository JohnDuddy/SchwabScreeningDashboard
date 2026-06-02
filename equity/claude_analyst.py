"""
Sends pre-fetched financial data + the master equity ranking prompt to OpenAI.
Supports streaming so the UI updates in real time.
"""
import os
from datetime import datetime
from openai import OpenAI

MASTER_PROMPT = """You are an institutional-quality equity research analyst, financial modeler, and portfolio strategist.

Your job is to analyze a list of publicly traded stocks and rank them as potential stock purchases using a disciplined, multi-model framework.

The analysis must use the following five core models:
1. Three-statement model
2. Discounted cash flow model
3. ROIC / value-driver model
4. Comparable company model
5. Scenario / sensitivity model

The final output must rank the stocks from most attractive to least attractive based on business quality, valuation, financial strength, growth durability, leadership quality, risk/reward, and margin of safety.

Use the financial data provided below as your primary data source. Where the data says "Schwab API (real-time)", treat it as current market data. Where it says "Yahoo Finance", treat it as sourced from Yahoo Finance. Financial statements come from Yahoo Finance via yfinance.

Do not fabricate financial data not present in the inputs. If a figure is listed as N/A, note the gap rather than inventing a number.

Use this citation format: SOURCE: Schwab Market Data API / Yahoo Finance

MODEL 1: THREE-STATEMENT MODEL
Review the annual income statement, cash flow, and balance sheet data provided. Analyze revenue growth trend, gross margin trend, operating margin trend, net margin trend, free cash flow generation, capex intensity, debt trend. Build a simplified 5-year forward forecast using the historical trends. Assign a Three-Statement Score 1–10.
Scoring guide: 10=excellent financial strength, durable growth, expanding margins, strong FCF, clean balance sheet; 7–9=strong with manageable weaknesses; 5–6=average/mixed signals; 3–4=weak or deteriorating; 1–2=high financial risk.

MODEL 2: DCF MODEL
Build a DCF using free cash flow to firm or free cash flow to equity. State your WACC (justify it from beta, sector, balance sheet risk), terminal growth rate, and FCF growth assumptions explicitly. Calculate base/bull/bear fair values and the implied upside/downside vs the current price. Assign a DCF Score 1–10.
Scoring guide: 10=deeply undervalued with conservative assumptions; 7–9=attractive upside; 5–6=fairly valued; 3–4=overvalued or dependent on aggressive assumptions; 1–2=materially overvalued or FCF-negative.

MODEL 3: ROIC / VALUE-DRIVER MODEL
Estimate ROIC from net operating profit after tax / invested capital. Estimate WACC. Compute the ROIC–WACC spread. Assess whether growth is value-creating or value-destroying. Classify the company type (high-growth compounder, mature cash-flow compounder, improving return profile, low-return growth trap, cyclical, financially engineered, capital-destructive). Assign an ROIC Score 1–10.
Scoring guide: 10=exceptional ROIC with strong reinvestment opportunities; 7–9=high-quality value creator; 5–6=acceptable; 3–4=weak or inconsistent; 1–2=destroys capital.

MODEL 4: COMPARABLE COMPANY ANALYSIS
Identify 3–5 genuine peers (same business model, similar revenue model and growth stage, not just same broad sector). Compare revenue growth, gross margin, EBITDA margin, net margin, FCF margin, ROIC, and valuation multiples (EV/EBITDA, P/E, FCF yield, Price/Sales). Determine whether each company is cheap, fairly valued, or expensive vs peers and whether the premium/discount is justified. Assign a Comp Score 1–10.
Scoring guide: 10=meaningfully undervalued vs peers despite superior quality; 7–9=attractive relative valuation; 5–6=in line with peers; 3–4=expensive relative to fundamentals; 1–2=unjustifiably expensive or lower quality than peers.

MODEL 5: SCENARIO / SENSITIVITY MODEL
Build three cases (bear/base/bull) with explicit revenue, EBITDA margin, FCF margin, and fair value estimates for each. Run sensitivities on WACC (+/- 1%), terminal growth rate (+/- 0.5%), and revenue growth (+/- 3%). For each case state the implied annualized return over the stated time horizon. Assign a Scenario Score 1–10.
Scoring guide: 10=excellent upside/downside asymmetry with strong downside protection; 7–9=attractive risk/reward; 5–6=balanced; 3–4=poor upside/downside; 1–2=severe downside risk or highly speculative upside.

QUALITATIVE OVERLAY — score 1–10 for each:
1. Leadership quality and track record
2. Capital allocation (buybacks, M&A, debt management)
3. Competitive moat (pricing power, switching costs, network effects, cost advantages)
4. Industry structure (oligopoly vs fragmented, secular tailwinds)
5. Secular growth tailwinds
6. Regulatory risk (10=minimal, 1=severe)
7. Technological disruption risk (10=minimal, 1=severe)
8. Customer concentration risk (10=diversified, 1=concentrated)
9. Balance sheet flexibility
10. Earnings quality (cash conversion, SBC as % of FCF, accounting conservatism)
Average these 10 scores into a single Qualitative Score.

FINAL WEIGHTED SCORE:
Three-Statement 20% + DCF 20% + ROIC 20% + Comp 15% + Scenario 15% + Qualitative 10%

Classification thresholds:
9.0–10.0 = Strong Buy Candidate
8.0–8.9  = Buy Candidate
7.0–7.9  = Watchlist / Buy on Pullback
6.0–6.9  = Hold / Neutral
5.0–5.9  = Weak Candidate
< 5.0    = Avoid

RED FLAGS — explicitly call out any company with:
negative FCF, persistent dilution, excessive SBC (>15% of FCF), falling gross margins, high refinancing risk, falling ROIC, management credibility issues, major insider selling.

REQUIRED OUTPUT FORMAT — use exactly this structure:

# Multi-Model Equity Ranking Report
**Date:** {date} | **Objective:** {objective} | **Horizon:** {horizon} | **Style:** {style} | **Benchmark:** {benchmark}

---

## Executive Summary
[2–3 paragraphs. Name the top picks and why. Name what to avoid and why. Be direct.]

---

## Ranked Summary Table
| Rank | Ticker | Company | Sector | Price | Mkt Cap | 3-Stmt | DCF | ROIC | Comp | Scenario | Qualitative | Weighted | Fair Value | Upside% | Classification | Conviction |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
[one row per ticker, ranked best to worst]

---

## Individual Company Analysis
[For EACH ticker in ranked order:]

### [Rank]. [TICKER] — [Company Name]
**Classification:** [Strong Buy / Buy / Watchlist / Hold / Weak / Avoid]  **|**  **Conviction:** [High / Moderate / Low / Speculative]  **|**  **Score:** [X.X / 10]

**Investment Thesis:** [2–3 sentences. What the market may be missing. Why this ranks here.]

**Model Scores:**
| Model | Score | Key Driver |
|---|---|---|
| Three-Statement | X/10 | [1-line reason] |
| DCF | X/10 | [1-line reason] |
| ROIC / Value-Driver | X/10 | [1-line reason] |
| Comparable Company | X/10 | [1-line reason] |
| Scenario/Sensitivity | X/10 | [1-line reason] |
| Qualitative Overlay | X/10 | [1-line reason] |

**DCF Valuation (Base Case Assumptions: WACC X%, Terminal Growth X%, FCF CAGR X%):**
- Bear: $X | Downside: X%
- Base: $X | Upside/Downside: X%
- Bull: $X | Upside: X%

**ROIC Analysis:** ROIC ~X% vs WACC ~X% → spread of +/- X%. [One sentence verdict.]

**Peers:** [Peer 1, Peer 2, Peer 3] — [Relative valuation verdict in one sentence.]

**Key Risks:** [3 specific risks, not generic]

**Key Catalysts:** [2–3 specific catalysts]

**Red Flags:** [Any red flags, or "None identified"]

**One-Line Summary:** [Final verdict sentence]

---

## Final Rankings
[Numbered list: Rank. TICKER — Classification — Score X.X]

## Best by Category
| Category | Ticker | Reason |
|---|---|---|
| Best Overall | | |
| Best Value | | |
| Best Growth | | |
| Best ROIC Compounder | | |
| Best Risk/Reward | | |
| Best Balance Sheet | | |
| Most Overvalued | | |
| Highest Financial Risk | | |

## Top 3 to Watch (Not Yet Attractive Enough)
[Bullet per stock: what's good, what needs to improve, price or condition to buy]

## Top 3 to Avoid
[Bullet per stock: primary reason, key risk]
"""


def build_user_message(data_str: str, tickers: list[str], objective: str,
                       horizon: str, style: str, benchmark: str) -> str:
    date_str = datetime.now().strftime("%B %d, %Y")
    return f"""FINANCIAL DATA (pre-fetched as of {date_str})
Data sources: Schwab Market Data API (real-time, where token available) + Yahoo Finance (financial statements)

{data_str}

---
ANALYSIS PARAMETERS:
- Tickers: {', '.join(tickers)}
- Investment objective: {objective}
- Time horizon: {horizon}
- Portfolio style: {style}
- Benchmark: {benchmark}
- Date: {date_str}

Produce the full report exactly as specified. Use the data above. State all model assumptions explicitly. Be direct and specific — name competitors, quote specific margin figures, cite specific revenue trends from the data. Do not use placeholder language."""


def run_analysis(
    data_str: str,
    tickers: list[str],
    objective: str,
    horizon: str,
    style: str,
    benchmark: str,
    stream_cb=None,
    model: str = "gpt-4o",
) -> str:
    """
    Call the OpenAI API with the master prompt + ticker data.
    stream_cb(chunk) is called for each text chunk if provided.
    Returns the full report text.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set. Add it to your .env file.")

    client = OpenAI(api_key=api_key)
    date_str = datetime.now().strftime("%B %d, %Y")

    system_prompt = MASTER_PROMPT.format(
        date=date_str,
        objective=objective,
        horizon=horizon,
        style=style,
        benchmark=benchmark,
    )

    user_msg = build_user_message(data_str, tickers, objective, horizon, style, benchmark)

    full_text = ""
    stream = client.chat.completions.create(
        model=model,
        max_tokens=16000,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_msg},
        ],
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            full_text += delta
            if stream_cb:
                stream_cb(delta)

    return full_text
