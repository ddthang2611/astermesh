# Astermesh Token Benchmark Report

## Target Codebase: `astermesh`
- Analyzed Source Files: 36
- Communities Discovered (Leiden): 23

## 1. Context Size Comparison
| Metric | Raw Codebase | Compact XML | Dense ACN Wire | ACN vs Raw |
| :--- | :--- | :--- | :--- | :--- |
| **Token Count** | 38,119 | 8,030 | 4,166 | **89.07% Saved** |
| **Character Count** | 166,262 | 20,721 | 12,431 | 92.52% Saved |
| **Line Count** | N/A | 301 lines | 25 lines | Minimal lines |
| **Single Community Avg** | N/A | ~112 tokens | ~112 tokens | Micro-context ready |

## 2. LLM Cost & Context Efficiency
- **Note**: This benchmark measures full-repository architectural context compression vs raw source dump (as opposed to targeted single-symbol grep).
- **Estimated Savings**: ~$101.86 USD per 1,000 full-context queries (at $3.00 / 1M input tokens).
