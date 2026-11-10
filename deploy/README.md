# MHTT 正式部署配置

MHTT 用自己的一套代码（本仓库）、自己的云服务器和库，和 AIIS 完全分开（[上线计划](../docs/deploy/正式上线计划.md)）。

| 文件 | 用途 |
|---|---|
| `docker-compose.yml` | 服务器上的整套服务：PostgreSQL、Odoo、Caddy（HTTPS）。只有 Caddy 对外开放 80/443 |
| `Caddyfile` | HTTPS 证书自动申请；`/websocket` 转到 8072，其余转到 8069 |
| `odoo.conf.template` | Odoo 配置模板：多进程、只认 MHTT 的库、不列出数据库 |
| `render_conf.sh` | 用 `.env` 生成 `odoo.conf`；密码还是占位符时拒绝生成 |
| `env.example` | `.env` 模板：域名、密码、发信地址 |
| `modules.txt` | 要安装的顶层模块；`scripts/fresh_install_check.sh` 发布前测的是同一份清单 |
| `first_install.sh` | 建正式库：先装沙特科目表，再装模块；库已存在时拒绝执行 |
| `brand.env`、`logo.png` | MHTT 品牌：深蓝 #233D7E 和 logo |
| `certs/` | MHTT 的 LDAP 根证书 `ldap-ca.pem` |
| `crontab.example` | 每晚备份，以及拷回公司内网的第二份备份 |

`.env` 和 `odoo.conf` 里有密码，只放在服务器上，不进 git。

## 上云步骤

以下命令都在服务器上的代码目录（例如 `/opt/odoo`）里执行，对应上线计划第 5 节的步骤 2–6。

```bash
# 步骤 2：服务器装好 Ubuntu 和 Docker；域名已指向本机公网 IP；防火墙只开 22（限办公室 IP）、80、443
git clone <本仓库地址> /opt/odoo && cd /opt/odoo

# 步骤 3：密码和域名
cp deploy/env.example deploy/.env && chmod 600 deploy/.env
vi deploy/.env                            # 填域名、两个随机密码、发信地址
deploy/render_conf.sh
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d
#   浏览器打开 https://<域名>/web/login：有 HTTPS 锁即可（此时还没有库）

# 步骤 4：建正式库
deploy/first_install.sh

# 品牌：logo 和深蓝主题色（先在 brand.env 里填公司全称）
docker cp deploy/logo.png mhtt-app:/tmp/brand-logo.png
docker exec -u root mhtt-app chmod 644 /tmp/brand-logo.png
docker exec -i --env-file deploy/brand.env -e BRAND_LOGO=/tmp/brand-logo.png \
    mhtt-app odoo shell -d mhtt --no-http < scripts/setup_company_brand.py

# LDAP（拿到 MHTT 的参数和根证书之后）：根证书放到 deploy/certs/ldap-ca.pem，然后
docker exec -i -e LDAP_SERVER=... -e LDAP_BASE=... mhtt-app odoo shell -d mhtt --no-http < scripts/setup_ldap.py

# 步骤 5：导入数据后检查，直到没有 FIX 项
docker exec -i mhtt-app odoo shell -d mhtt --no-http < scripts/post_import_check.py

# 步骤 6：每晚备份
crontab -e                                # 按 deploy/crontab.example 填
```

## 升级（发布新版本）

1. 在开发服务器上跑 `scripts/fresh_install_check.sh`，结果为 PASS；需要时用 MHTT 备份的拷贝做一次升级测试（上线计划第 8 节）。
2. 服务器上：先备份 `scripts/backup.sh mhtt`，然后 `git pull`，再执行
   `docker exec mhtt-app odoo -d mhtt --no-http --workers=0 --max-cron-threads=0 --stop-after-init -u <改动的模块>`，最后 `docker restart mhtt-app`。
