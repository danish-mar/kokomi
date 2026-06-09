import os
import sys
import re
import tomllib
from pathlib import Path

def main():
    # Tag name could be like "v5.0.1"
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if not tag and len(sys.argv) > 1:
        tag = sys.argv[1]
        
    if not tag:
        print("Error: No tag specified (neither GITHUB_REF_NAME env nor argv[1] provided).", file=sys.stderr)
        sys.exit(1)
        
    ver_num = tag.lstrip('v')
    
    # Read release name from pyproject.toml
    pyproject_path = Path("pyproject.toml")
    if not pyproject_path.exists():
        print("Error: pyproject.toml not found.", file=sys.stderr)
        sys.exit(1)
        
    with open(pyproject_path, "rb") as f:
        pyproject_data = tomllib.load(f)
        release_name = pyproject_data.get("project", {}).get("release-name", "")
        
    # Read CHANGELOG.md
    changelog_path = Path("CHANGELOG.md")
    if not changelog_path.exists():
        print("Error: CHANGELOG.md not found.", file=sys.stderr)
        sys.exit(1)
        
    changelog_content = changelog_path.read_text(encoding="utf-8")
    
    # Extract the matching version block
    # Matches: ## [v<version>] - YYYY-MM-DD or ## [v<version>]
    # until the next ## [v... or end of file
    pattern = rf"(## \[v?{re.escape(ver_num)}\].*?)(?=\n## \[v|\Z)"
    match = re.search(pattern, changelog_content, re.DOTALL)
    
    if match:
        release_notes = match.group(1).strip()
    else:
        release_notes = f"Release {tag}"
        print(f"Warning: Changelog entry for {ver_num} not found in CHANGELOG.md", file=sys.stderr)

    # Write release notes to file
    Path("release_notes.md").write_text(release_notes, encoding="utf-8")
    
    # Determine the release title
    if release_name:
        release_title = f"{tag} ({release_name})"
    else:
        release_title = tag
        
    # Output values for GitHub Actions
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"title={release_title}\n")
            
    print(f"Title: {release_title}")
    print("Release notes written to release_notes.md")

if __name__ == "__main__":
    main()
