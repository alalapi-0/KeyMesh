#!/usr/bin/env pwsh
# 说明：所有生成的密钥将位于 ./keys/ 目录下，该目录已在 .gitignore 中忽略，禁止提交。

param(
    [Parameter(Mandatory = $true)]
    [string]$NodeId,
    [int]$CaDays = 3650,
    [int]$NodeDays = 825
)

# 检查 openssl 是否可用，如未安装请参考 https://slproweb.com/products/Win32OpenSSL.html
if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
    Write-Error "未找到 openssl，请先安装后重试。"
    exit 1
}

$KeyDir = Join-Path -Path (Get-Location) -ChildPath "keys"  # 计算密钥目录
New-Item -ItemType Directory -Path $KeyDir -Force | Out-Null  # 创建目录（存在则忽略）

$CaKey = Join-Path $KeyDir "ca.key"  # CA 私钥
$CaCrt = Join-Path $KeyDir "ca.crt"  # CA 证书
$NodeKey = Join-Path $KeyDir "$NodeId.key"  # 节点私钥
$NodeCsr = Join-Path $KeyDir "$NodeId.csr"  # 节点 CSR
$NodeCrt = Join-Path $KeyDir "$NodeId.crt"  # 节点证书

if ((Test-Path $NodeKey) -or (Test-Path $NodeCrt)) {
    Write-Error "检测到 $NodeId 相关证书已存在，为避免覆盖请手动删除后再执行。"
    exit 1
}

if (-not ((Test-Path $CaKey) -and (Test-Path $CaCrt))) {
    Write-Host "生成新的 CA 证书..."
    openssl genrsa -out $CaKey 4096 | Out-Null  # 生成 CA 私钥
    openssl req -x509 -new -nodes -key $CaKey -sha256 -days $CaDays -out $CaCrt -subj "/CN=KeyMesh-CA" | Out-Null  # 自签 CA 证书
} else {
    Write-Host "复用现有 CA 证书。"
}

Write-Host "生成节点私钥和 CSR..."
openssl genrsa -out $NodeKey 4096 | Out-Null  # 节点私钥
openssl req -new -key $NodeKey -out $NodeCsr -subj "/CN=$NodeId" | Out-Null  # 节点 CSR

Write-Host "使用 CA 签发节点证书..."
openssl x509 -req -in $NodeCsr -CA $CaCrt -CAkey $CaKey -CAcreateserial -out $NodeCrt -days $NodeDays -sha256 | Out-Null  # 使用 CA 签名

Write-Host "证书生成完成:"  # 输出结果
Write-Host "  CA 证书: $CaCrt"
Write-Host "  节点证书: $NodeCrt"
Write-Host "  节点私钥: $NodeKey"
Write-Host "  CSR 文件: $NodeCsr"
