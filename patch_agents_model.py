#!/usr/bin/env python3
"""
patch_agents_model.py — Remove o campo 'model:' do frontmatter YAML de todos
os arquivos de agente em .claude/agents/*.md

Comportamento:
  - Sem 'model:' no frontmatter, o Claude Code usa o modelo passado via
    --model na linha de comando (injetado pelo runner.py a partir do
    providers.json).
  - Isso permite que qualquer LLM do OpenRouter (300+) ou de outros provedores
    seja usado transparentemente, sem editar cada arquivo de agente.
  - Para o provider nativo Anthropic, o runner passa active_model=None e o
    Claude Code usa o default da conta (ex: claude-sonnet-4).

Uso:
    python3 patch_agents_model.py [--dry-run] [--agents-dir PATH]
"""
import re
import sys
import argparse
from pathlib import Path


def patch_file(path: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Remove 'model:' line from YAML frontmatter of a .md agent file.

    Returns (changed: bool, old_model: str | "")
    """
    text = path.read_text(encoding="utf-8")

    # Only process files that have YAML frontmatter (--- ... ---)
    if not text.startswith("---"):
        return False, ""

    # Find the end of frontmatter
    end_match = re.search(r"\n---", text[3:])
    if not end_match:
        return False, ""

    frontmatter_end = 3 + end_match.start()
    frontmatter = text[3:frontmatter_end]
    rest = text[frontmatter_end:]

    # Find and capture the model line
    model_match = re.search(r"\nmodel:\s*\S+", frontmatter)
    if not model_match:
        return False, ""  # Nothing to change

    old_model = model_match.group(0).strip().replace("model:", "").strip()

    # Remove the model line
    new_frontmatter = frontmatter[:model_match.start()] + frontmatter[model_match.end():]
    new_text = "---" + new_frontmatter + rest

    if not dry_run:
        path.write_text(new_text, encoding="utf-8")

    return True, old_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    parser.add_argument("--agents-dir", default=None, help="Path to .claude/agents/ directory")
    args = parser.parse_args()

    # Auto-detect agents dir: script location -> project root -> .claude/agents
    if args.agents_dir:
        agents_dir = Path(args.agents_dir)
    else:
        # Try common locations
        candidates = [
            Path(__file__).parent / ".claude" / "agents",
            Path("/workspace/.claude/agents"),
            Path("/opt/evo-nexus/.claude/agents"),
        ]
        agents_dir = next((p for p in candidates if p.is_dir()), None)
        if not agents_dir:
            print("ERROR: Could not find .claude/agents/ directory. Use --agents-dir", file=sys.stderr)
            sys.exit(1)

    md_files = sorted(agents_dir.glob("*.md"))
    if not md_files:
        print(f"No .md files found in {agents_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Patching {len(md_files)} agent files in {agents_dir}\n")

    changed = 0
    for f in md_files:
        was_changed, old_model = patch_file(f, dry_run=args.dry_run)
        if was_changed:
            status = "WOULD REMOVE" if args.dry_run else "PATCHED"
            print(f"  {status}: {f.name}  (model: {old_model} -> dynamic from providers.json)")
            changed += 1
        else:
            print(f"  SKIP: {f.name}  (no model field)")

    print(f"\n{'Would change' if args.dry_run else 'Changed'}: {changed}/{len(md_files)} files")
    if not args.dry_run and changed > 0:
        print("\nAll agents will now use the model configured in config/providers.json at runtime.")
        print("To switch models: update providers.json 'OPENAI_MODEL' (or equivalent) — no agent editing needed.")


if __name__ == "__main__":
    main()
