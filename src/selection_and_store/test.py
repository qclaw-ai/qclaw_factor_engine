import os

# 模拟当前 runner 里的路径
runner_dir = os.path.dirname(__file__)              # src/selection_and_store
old_project_root = os.path.abspath(os.path.join(runner_dir, ".."))
new_project_root = os.path.abspath(os.path.join(runner_dir, "..", ".."))

# 用你实际的一条 md 绝对路径替换这里
fd_doc_path = r"D:\programer\tdenergy\repo\qclaw_factor_engine\factor_docs\md\JoinQuant_alpha191_exact\JQ_ALPHA_000.md"

print("old_project_root:", old_project_root)
print("new_project_root:", new_project_root)
print("rel from src/ .. :", os.path.relpath(fd_doc_path, start=old_project_root))
print("rel from repo root:", os.path.relpath(fd_doc_path, start=new_project_root))