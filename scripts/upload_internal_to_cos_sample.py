import os
from qcloud_cos import CosConfig, CosS3Client

# -------------------------- 配置项（改你自己的密钥即可）--------------------------
SECRET_ID = "你的子账号SecretId"
SECRET_KEY = "你的子账号SecretKey"
REGION = "ap-shanghai"
BUCKET = "factor-data-1324221249"

# 你服务器本地 文件根目录
LOCAL_ROOT = "/xxx/xxx/factor_export_parquet"
# COS 目标根目录（保持和你现在一致）
COS_ROOT = "factor_export_parquet"

# 👉 关键：上海 内网 Endpoint
INTERNAL_ENDPOINT = "factor-data-1324221249.cos-internal.ap-shanghai.myqcloud.com"
# --------------------------------------------------------------------------------

# 初始化COS客户端（走内网）
config = CosConfig(
    Region=REGION,
    SecretId=SECRET_ID,
    SecretKey=SECRET_KEY,
    Endpoint=INTERNAL_ENDPOINT
)
cos_client = CosS3Client(config)


def upload_folder(local_dir, cos_dir):
    """递归整目录上传，保留原目录结构"""
    for root, _, files in os.walk(local_dir):
        for file_name in files:
            local_file_path = os.path.join(root, file_name)
            # 计算COS相对路径
            rel_path = os.path.relpath(local_file_path, local_dir)
            cos_file_key = os.path.join(cos_dir, rel_path).replace("\\", "/")

            # 上传文件
            cos_client.upload_file(
                Bucket=BUCKET,
                LocalFilePath=local_file_path,
                Key=cos_file_key
            )
            print(f"上传成功：{cos_file_key}")


if __name__ == "__main__":
    upload_folder(LOCAL_ROOT, COS_ROOT)
    print("✅ 全部上传完成")