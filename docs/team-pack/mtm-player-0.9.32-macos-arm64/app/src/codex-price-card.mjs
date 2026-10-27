// OpenAI ChatGPT Work and Codex Business/Enterprise/Edu Standard rates.
// Retrieved 2026-09-28. Credits per million tokens; not account billing evidence.
// https://help.openai.com/en/articles/11481834-chatgpt-rate-card-business-enterpriseedu-credit-based-pricing
const rates = {
  'gpt-6-astra': [250, 25, 1250],
  'gpt-6-sol': [50, 5, 250],
  'gpt-6-luna': [2.5, 0.25, 12.5],
  'gpt-5.6-sol': [100, 10, 500],
  'gpt-5.6-terra': [50, 5, 300],
  'gpt-5.6-luna': [5, 0.5, 30],
  'gpt-5.5': [125, 12.5, 750],
  'gpt-5.4': [62.5, 6.25, 375],
  'gpt-5.4-mini': [18.75, 1.875, 113],
  'gpt-5.3-codex': [43.75, 4.375, 350],
  'gpt-5.2': [43.75, 4.375, 350],
};
export const codexStandardPriceCard = Object.freeze({
  schemaVersion: 1, id: 'codex-standard-2026-09-28', currency: 'CREDITS', unitTokens: 1_000_000,
  models: Object.freeze(Object.fromEntries(Object.entries(rates).map(([model, [input, cachedInput, output]]) =>
    [model, Object.freeze({ input, cachedInput, output })]))),
});
