// Maps app/tools.py's TOOL_BELT names to display labels. Kept in sync by
// hand with the four @tool functions in app/tools.py -- update both places
// together if a tool is renamed or added.
export function toolLabel(name?: string): string {
  switch (name) {
    case "search_filings":
      return "Filings search";
    case "search_filings_exact":
      return "Exact filings search";
    case "search_live_news":
      return "Live news";
    case "get_market_data":
      return "Market data";
    default:
      return name ?? "tool";
  }
}
