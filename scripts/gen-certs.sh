#!/usr/bin/env bash
# 说明：生成的密钥保存在 ./keys/ 中，已被 .gitignore 忽略，请勿提交到版本库。

set -euo pipefail  # 遇到错误立即退出，未定义变量视为错误

NODE_ID="${1:-}"  # 第一个参数作为节点 ID
CA_DAYS=${CA_DAYS:-3650}  # CA 证书有效期（天），可通过环境变量覆盖
NODE_DAYS=${NODE_DAYS:-825}  # 节点证书有效期（天），可通过环境变量覆盖
KEY_DIR="keys"  # 密钥输出目录

if [[ -z "${NODE_ID}" ]]; then
  echo "用法: $0 <node-id>"  # 缺少参数时给出提示
  exit 1  # 退出并返回错误码
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "未找到 openssl，请先安装 openssl 再运行此脚本。"  # 检查 openssl 是否可用
  exit 1  # 退出并返回错误码
fi

mkdir -p "${KEY_DIR}"  # 创建密钥目录

CA_KEY="${KEY_DIR}/ca.key"  # CA 私钥路径
CA_CRT="${KEY_DIR}/ca.crt"  # CA 证书路径
NODE_KEY="${KEY_DIR}/${NODE_ID}.key"  # 节点私钥路径
NODE_CSR="${KEY_DIR}/${NODE_ID}.csr"  # 节点证书签名请求
NODE_CRT="${KEY_DIR}/${NODE_ID}.crt"  # 节点证书

if [[ -f "${NODE_KEY}" || -f "${NODE_CRT}" ]]; then
  echo "检测到 ${NODE_ID} 相关文件已存在，出于安全考虑不覆盖。"  # 如果节点证书已存在则拒绝覆盖
  exit 1  # 退出并返回错误码
fi

if [[ ! -f "${CA_KEY}" || ! -f "${CA_CRT}" ]]; then
  echo "生成新的 CA 证书..."  # 提示正在生成 CA
  openssl genrsa -out "${CA_KEY}" 4096  # 生成 4096 位 CA 私钥
  openssl req -x509 -new -nodes -key "${CA_KEY}" -sha256 -days "${CA_DAYS}" -out "${CA_CRT}" -subj "/CN=KeyMesh-CA"  # 使用自签方式生成 CA 证书
else
  echo "检测到已有 CA 证书，复用现有文件。"  # 如已有 CA 则复用
fi

echo "生成节点私钥和 CSR..."  # 提示生成节点材料
openssl genrsa -out "${NODE_KEY}" 4096  # 生成节点私钥
openssl req -new -key "${NODE_KEY}" -out "${NODE_CSR}" -subj "/CN=${NODE_ID}"  # 生成证书签名请求

echo "使用 CA 签发节点证书..."  # 提示签发证书
openssl x509 -req -in "${NODE_CSR}" -CA "${CA_CRT}" -CAkey "${CA_KEY}" -CAcreateserial -out "${NODE_CRT}" -days "${NODE_DAYS}" -sha256  # 使用 CA 为节点签名

echo "证书生成完成："  # 打印结果
printf '  CA 证书: %s\n' "${CA_CRT}"
printf '  节点证书: %s\n' "${NODE_CRT}"
printf '  节点私钥: %s\n' "${NODE_KEY}"
printf '  CSR 文件: %s\n' "${NODE_CSR}"
