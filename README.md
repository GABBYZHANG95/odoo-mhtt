# MHTT Odoo（Community 17）

MHTT（Mastery House）的 Odoo 系统：自己的一套代码，自己的云服务器和库。2026-11-10 从 AIIS 的代码复制出来，之后独立修改，**不和 AIIS 同步**。模块技术名暂时保留 `aiis_*`，界面上显示 MHTT。

- 正式部署：见 [deploy/README.md](deploy/README.md) 和 [上线计划](docs/deploy/正式上线计划.md)
- 业务设计：[docs/design/审批流程设计.md](docs/design/审批流程设计.md)

## 开发环境（内网开发服务器）

和 AIIS 的测试环境并排运行，各用各的容器、数据卷和端口：

| | MHTT 开发环境 |
|---|---|
| 地址 | http://10.55.223.160:8169 |
| 容器 | `mhtt-app`、`mhtt-db`（`docker compose` 项目名 `mhtt`） |
| 库 | `mhtt`，不带演示数据，沙特科目表；只放测试数据，真实数据不导进来 |

```bash
docker compose up -d                       # 启动（在本目录）
docker exec mhtt-app odoo -d mhtt --no-http --stop-after-init -u <模块名>
docker restart mhtt-app                    # 升级后必须重启，否则还是旧代码
```

## 目录说明

- `docker-compose.yml` — Odoo + PostgreSQL 服务编排（数据库密码从 `.env` 读取）
- `.env` — 数据库密码（不进 git，请勿外泄）
- `config/odoo.conf` — Odoo 配置文件（含数据库连接、管理员密码等，不进 git，请勿外泄）
- `addons/` — 自定义插件目录（挂载为 `/mnt/extra-addons`），MHTT 的定制模块都在这里（技术名 `aiis_*`）
- `addons/oca/` — 固定版本的 OCA 模块（财务报表、资产、银行对账等，来源见其中的 README）。`config/odoo.conf` 的 `addons_path` 需要包含它：`addons_path = /mnt/extra-addons,/mnt/extra-addons/oca,/usr/lib/python3/dist-packages/odoo/addons`
- `branding/` — MHTT logo（`mhtt-logo.png`，透明背景；部署时 `scripts/setup_company_brand.py` 用它设置公司 Logo）
- `docs/deploy/正式上线计划.md` — 正式上线计划（云服务器、正式库、备份、一个月试用）
- `scripts/backup.sh` — 备份脚本
- `scripts/fresh_install_check.sh` — 全新安装检查：临时空库 + 沙特科目表 + 全部模块和测试，每次发布前跑
- `scripts/post_import_check.py` — 正式库导入数据后的检查清单（只读）
- `scripts/setup_company_brand.py` — 设置本库的 Logo 和品牌色（公司主色；界面、看板、打印单据都跟随它）
- `deploy/` — MHTT 正式部署配置：docker-compose（PostgreSQL、Odoo、Caddy）、odoo.conf 模板、建库脚本、品牌，步骤见 `deploy/README.md`
- `backups/` — 备份文件（不进 git）

改完插件后升级并**重启**，否则正在运行的 Odoo 还是旧代码（浏览器会报 "field is undefined"）：

```bash
docker exec mhtt-app odoo -d mhtt --no-http --stop-after-init -u <模块名>
docker restart mhtt-app
```

## LDAP 登录

员工用公司目录（LDAP）的账号密码登录，Odoo 不保存这个密码。`aiis_access` 依赖 Odoo 自带的 `auth_ldap`，
每个数据库连哪台 LDAP 由 `scripts/setup_ldap.py` 设置（参数见脚本开头）：

```bash
docker exec -i -e LDAP_SERVER=<MHTT LDAP 主机> -e LDAP_BASE=<Base DN> -e LDAP_CHECK_LOGIN=<一个登录名> \
    mhtt-app odoo shell -d mhtt --no-http < scripts/setup_ldap.py
```

- 连接用 StartTLS（389 端口），证书由 `deploy/certs/ldap-ca.pem` 校验（`deploy/docker-compose.yml` 里的 `LDAPTLS_CACERT`）。MHTT 的 LDAP 参数和根证书待提供。
- 首次登录**不会**自动建用户：先在 Odoo 建用户（登录名 = LDAP 的 uid 或邮箱）并关联员工档案、分配权限。
- 本地密码仍然有效：admin 保留本地密码作应急账号，LDAP 不通时也能登录。
- 改密码、重置密码在 LDAP 里做；员工离职在 LDAP 停用后，Odoo 里也把用户归档。
- MHTT 开发环境暂不连 LDAP；部署时连 MHTT 自己的 LDAP。

## 备份与恢复

手动备份运行 `scripts/backup.sh`，备份到 `backups/`，自动清理 14 天前的备份。

目前是测试环境，**没有开启自动备份**。正式库上线后，用 crontab 每天执行一次：

```
30 2 * * * /home/gzhang/odoo-demo/scripts/backup.sh >> /home/gzhang/odoo-demo/backups/backup.log 2>&1
```

> 备份只存在本机，服务器硬盘坏了会一起丢失。正式上线前要加一份异地拷贝。

恢复到一个新数据库（不覆盖现有库）：

```bash
docker exec mhtt-db createdb -U odoo odoo_restored
docker exec -i mhtt-db pg_restore -U odoo -d odoo_restored --no-owner < backups/odoo_<时间>.dump
docker exec mhtt-app mkdir -p /var/lib/odoo/filestore/odoo_restored
docker exec -i mhtt-app tar -xzf - -C /var/lib/odoo/filestore/odoo_restored --strip-components=1 < backups/odoo_<时间>_filestore.tar.gz
```

## 版本管理

本目录是一个本地 git 仓库（只在本机，没有推送到任何远程仓库）。
`config/odoo.conf`、`.env`、`backups/` 已排除在外。

## 关于版本

这是 **Odoo Community 版**（官方 `odoo:17` 镜像），完全免费开源（LGPLv3），
可合法用于企业内部运营，无需订阅付费。

如需 Odoo **Enterprise** 版特有功能（高级会计报表、部分行业模块等），
需要向 Odoo 官方购买订阅才能合法使用；本项目不包含、也不用于绕过 Enterprise 的许可校验。

## 安全提醒

`config/odoo.conf` 中的 `admin_passwd` 及数据库密码为明文，建议：
- 不要将本项目推送到公共代码仓库
- 生产 / 正式对外使用前更换为更强的密码，并考虑用环境变量注入而非明文写入配置文件
