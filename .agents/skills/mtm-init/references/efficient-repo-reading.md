# Efficient Repository Reading & Search (Lean2 Policy)

1. **Search First**:
   - Use `rg "pattern"` to find symbol definitions, call sites, or error strings.
   - Use `git status` or `find . -name "*.py"` to understand structure without reading file contents.

2. **Bounded File Reads**:
   - Never print whole files exceeding 100 lines.
   - Use line range reading (`sed -n '50,120p' file.py` or targeted head/tail).

3. **Output Cap**:
   - Codex config `-c tool_output_token_limit=2000` enforces context protection.
   - Pipe noisy commands through `head -n 50` or `tail -n 50`.
