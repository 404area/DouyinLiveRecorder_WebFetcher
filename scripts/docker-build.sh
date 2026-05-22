#!/usr/bin/env bash
# Docker 镜像构建
#   默认：自适应本机 CPU 架构，构建并加载到本地
#   --all-platforms：构建全部平台（本地分标签 / 配合 --push 生成 manifest）
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE_NAME="${IMAGE_NAME:-douyin-live-recorder}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
ALL_PLATFORMS="${ALL_PLATFORMS:-0}"
PUSH="${PUSH:-0}"

usage() {
  cat <<'EOF'
用法: docker-build.sh [选项]

默认自动检测本机 CPU，仅构建本架构镜像并加载到本地 Docker。

选项:
  -a, --all-platforms   构建全部平台（默认 amd64 + arm64）
  -p, --push            推送到镜像仓库（全平台时生成多架构 manifest）
  -n, --name NAME       镜像名（默认: douyin-live-recorder）
  -t, --tag TAG         镜像标签（默认: latest）
      --platforms LIST  自定义平台，逗号分隔（配合 -a 使用）
  -h, --help            显示帮助

环境变量（与参数等价）:
  IMAGE_NAME  IMAGE_TAG  PLATFORMS  ALL_PLATFORMS=1  PUSH=1

示例:
  ./scripts/docker-build.sh
  ./scripts/docker-build.sh -a
  ./scripts/docker-build.sh -a -p -n youruser/douyin-live-recorder
EOF
}

detect_native_platform() {
  case "$(uname -m)" in
    arm64|aarch64) echo "linux/arm64" ;;
    x86_64|amd64)  echo "linux/amd64" ;;
    *)             echo "linux/amd64" ;;
  esac
}

platform_suffix() {
  # linux/amd64 -> amd64
  echo "${1#linux/}"
}

ensure_buildx() {
  if ! docker buildx version &>/dev/null; then
    echo "错误: 需要 Docker Buildx" >&2
    exit 1
  fi
  if ! docker buildx inspect douyin-builder &>/dev/null; then
    docker buildx create --name douyin-builder --driver docker-container --use
  else
    docker buildx use douyin-builder
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -a|--all-platforms) ALL_PLATFORMS=1; shift ;;
    -p|--push)          PUSH=1; shift ;;
    -n|--name)          IMAGE_NAME="$2"; shift 2 ;;
    -t|--tag)           IMAGE_TAG="$2"; shift 2 ;;
    --platforms)        PLATFORMS="$2"; shift 2 ;;
    -h|--help)          usage; exit 0 ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

TAG="${IMAGE_NAME}:${IMAGE_TAG}"
NATIVE_PLATFORM="$(detect_native_platform)"

if ! docker version &>/dev/null; then
  echo "错误: 无法连接 Docker，请先启动 Docker Desktop" >&2
  exit 1
fi

# ---------- 全平台 ----------
if [[ "${ALL_PLATFORMS}" == "1" ]]; then
  if [[ "${PUSH}" == "1" ]]; then
    ensure_buildx
    echo "多架构构建并推送: ${TAG}"
    echo "平台: ${PLATFORMS}"
    docker buildx build \
      --platform "${PLATFORMS}" \
      --tag "${TAG}" \
      --file Dockerfile \
      --progress=plain \
      --push \
      .
  else
    ensure_buildx
    echo "多架构本地构建（各平台独立标签，避免 manifest list 无法 --load）"
    echo "平台: ${PLATFORMS}"
    IFS=',' read -ra PLATFORM_LIST <<< "${PLATFORMS}"
    for platform in "${PLATFORM_LIST[@]}"; do
      suffix="$(platform_suffix "${platform}")"
      platform_tag="${TAG}-${suffix}"
      echo "→ ${platform} → ${platform_tag}"
      docker buildx build \
        --platform "${platform}" \
        --tag "${platform_tag}" \
        --file Dockerfile \
        --progress=plain \
        --load \
        .
    done
    echo ""
    echo "已生成:"
    for platform in "${PLATFORM_LIST[@]}"; do
      echo "  ${TAG}-$(platform_suffix "${platform}")"
    done
    echo "本机可直接运行: ${TAG}-$(platform_suffix "${NATIVE_PLATFORM}")"
  fi
  echo "完成"
  exit 0
fi

# ---------- 本机架构（默认） ----------
if [[ "${PUSH}" == "1" ]]; then
  ensure_buildx
  echo "构建并推送本机架构 (${NATIVE_PLATFORM}): ${TAG}"
  docker buildx build \
    --platform "${NATIVE_PLATFORM}" \
    --tag "${TAG}" \
    --file Dockerfile \
    --progress=plain \
    --push \
    .
else
  echo "构建本机架构 (${NATIVE_PLATFORM}): ${TAG}"
  docker build \
    --platform "${NATIVE_PLATFORM}" \
    -t "${TAG}" \
    -f Dockerfile \
    .
fi

echo "完成: ${TAG}"
