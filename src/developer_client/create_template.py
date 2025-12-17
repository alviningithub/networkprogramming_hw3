import shutil
import os
import re

str = input("please enter the desire file name :").strip()
m = re.match(r"[A-za-z0-9\-\_]+",str).group(0)

if m != str:
    print("file name not allow")
    os._exit(0)

os.chdir("src/developer_client")
if os.path.exists(f"games/{str}"):
    print("path exist")
    os._exit(0)

# copy file into 
shutil.copytree("template",f"games/{str}")

# init uv
os.chdir(f"games/{str}")
os.system("uv init --bare")

os.system("uv lock")








