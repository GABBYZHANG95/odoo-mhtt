#!/usr/bin/env bash
# 全新安装检查：在一个临时空库上按正式部署的方式装一遍，证明任何时候都能建一个干净的正式库。
#   1. 新建空库，不带演示数据
#   2. 先装沙特科目表 l10n_sa（正式库的顺序：科目表在前，有了凭证就不能换）
#   3. 再装全部 AIIS 模块，并跑这些模块的自动化测试
#   4. 对比开发库（REF_DB，默认 mhtt），列出那边装了、这边没被依赖带上的自定义模块
#   5. 删掉临时库和它的附件目录（KEEP=1 保留，方便排查）
#
# 用法: scripts/fresh_install_check.sh            # 装 + 测试
#       TESTS=0 scripts/fresh_install_check.sh    # 只装，不跑测试（快）
# 结果: 最后一行 PASS / FAIL；日志在 backups/fresh_check/（不进 git）
# 每次发布前跑一遍（docs/deploy/正式上线计划.md 第 8 节）。
set -uo pipefail

DB="fresh_check_$(date +%Y%m%d_%H%M%S)"
TESTS="${TESTS:-1}"
KEEP="${KEEP:-0}"
REF_DB="${REF_DB:-mhtt}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/backups/fresh_check"
# 顶层模块，和正式部署用同一份清单（deploy/first_install.sh）；其余 AIIS / OCA 模块由依赖带进来
MODULES="$(tr -d ' \n' < "$ROOT/deploy/modules.txt")"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$DB.log"
ODOO=(docker exec mhtt-app odoo -d "$DB" --db-filter="^$DB\$" --no-http --max-cron-threads=0 --stop-after-init --without-demo=all)

cleanup() {
    if [ "$KEEP" = "1" ]; then
        echo "保留临时库 $DB"
        return
    fi
    docker exec mhtt-db dropdb -U odoo --if-exists "$DB" >/dev/null 2>&1
    docker exec mhtt-app rm -rf "/var/lib/odoo/filestore/$DB"
}
trap cleanup EXIT

fail() { echo "FAIL: $1（日志: $LOG）"; exit 1; }

echo "临时库 $DB，日志 $LOG"
docker exec mhtt-db createdb -U odoo "$DB" || fail "建库失败"

echo "[1/3] 安装沙特科目表 l10n_sa ..."
"${ODOO[@]}" -i l10n_sa >>"$LOG" 2>&1 || fail "l10n_sa 安装失败"

echo "[2/3] 安装 AIIS 模块 ..."
"${ODOO[@]}" -i "$MODULES" >>"$LOG" 2>&1
STATUS=$?
if [ $STATUS -eq 0 ] && [ "$TESTS" = "1" ]; then
    # 装好之后再单独跑 AIIS 模块的测试：安装时开测试会加载 OCA 模块的测试代码
    # （缺 odoo_test_helper）；测试模式要起 HTTP，用 8099 端口避开正在运行的服务
    echo "      运行 AIIS 模块的自动化测试 ..."
    AIIS=$(ls -d "$ROOT"/addons/aiis_* | xargs -n1 basename | paste -sd,)
    TAGS=$(echo "$AIIS" | tr ',' '\n' | sed 's|^|/|' | paste -sd,)
    docker exec mhtt-app odoo -d "$DB" --db-filter="^$DB\$" --http-port=8099 --gevent-port=8098 \
        --max-cron-threads=0 --stop-after-init -u "$AIIS" --test-enable --test-tags "$TAGS" >>"$LOG" 2>&1
    STATUS=$?
fi

ERRORS=$(grep -cE " (ERROR|CRITICAL) $DB " "$LOG")
grep -E " (ERROR|CRITICAL) $DB " "$LOG" | head -20
[ $STATUS -eq 0 ] || fail "模块安装或测试运行失败"
[ "$ERRORS" -eq 0 ] || fail "日志里有 $ERRORS 条 ERROR / CRITICAL"

CURRENCY=$(docker exec mhtt-db psql -U odoo -d "$DB" -tAc "select c.name from res_company co join res_currency c on c.id = co.currency_id order by co.id limit 1")
echo "公司本位币: $CURRENCY"
[ "$CURRENCY" = "SAR" ] || fail "本位币不是 SAR"

echo "[3/3] 对比开发库 $REF_DB 的自定义模块 ..."
SQL="select name from ir_module_module where state='installed' and (name like 'aiis_%' or author ilike '%OCA%' or name = 'hr_salary_in_work_info') order by name"
MISSING=$(comm -23 <(docker exec mhtt-db psql -U odoo -d "$REF_DB" -tAc "$SQL") <(docker exec mhtt-db psql -U odoo -d "$DB" -tAc "$SQL"))
if [ -n "$MISSING" ]; then
    echo "开发库装了、全新安装没装上的模块："
    echo "$MISSING"
    fail "模块清单不一致，补进 MODULES"
fi

if [ "$TESTS" = "1" ]; then
    grep -E "tests when loading database|failed, .* error\(s\)" "$LOG" | tail -3
fi
echo "PASS"
