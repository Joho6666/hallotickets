#!/bin/bash
# 使用自然语言提示词驱动大麦 mobile 流程
# 用法:
#   ./mobile/scripts/run_from_prompt.sh "帮张志涛抢一张 4 月 6 号张杰的演唱会门票，内场"
#   ./mobile/scripts/run_from_prompt.sh --mode probe --yes "帮张志涛抢一张 4 月 6 号张杰的演唱会门票，内场"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 启动预检（U-05）：与 start_ticket_grabbing.sh 共享同一函数库。
# 这里只做 adb 存在性检查（含 ANDROID_HOME 软化：有 adb 即放行）；
# serial 精确校验与占位符处理留给 prompt_runner Python 层
# （其自带单设备自动识别与模板 bootstrap 语义）。
source "$SCRIPT_DIR/lib/preflight.sh"
preflight_check_adb || exit 1

cd "$REPO_ROOT"
poetry run python mobile/prompt_runner.py "$@"
