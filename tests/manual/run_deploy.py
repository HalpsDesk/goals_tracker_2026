import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deploy.deploy_to_gh_pages import deploy_site

if __name__ == "__main__":
    # choose whichever folder you built into
    out_dir = Path("tests/tmp/site_out")  # or Path("site_out")
    result = deploy_site(out_dir=out_dir)
    print(result)
