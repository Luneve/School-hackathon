"""Run after pip install to apply local patches to third-party packages."""
import os
import shutil
import edupage_api

pkg_dir = os.path.dirname(edupage_api.__file__)
shutil.copy("patches/edupage_api_grades.py", os.path.join(pkg_dir, "grades.py"))
print(f"Patched edupage_api/grades.py in {pkg_dir}")
