#!/bin/bash
# 给 compose 中创建的应用账号授予两个库的权限。
#
# 为什么单独拆一个脚本：
#   - 官方 mysql 镜像只有在指定了 MYSQL_DATABASE 时才给 MYSQL_USER 授权，
#     而我们有两个库（meta 元数据库 + dw 数据仓库），该机制覆盖不到；
#   - 初始 SQL 里写死了 GRANT ... TO '<固定账号>'@'%'，与本项目账号不一致，
#     且 MySQL 8 不允许用 GRANT 隐式创建用户（ERROR 1410），会中断整个初始化流程。
#
# 文件名以 zz- 开头，保证在 dw.sql / meta.sql 之后执行（entrypoint 按文件名排序）。
set -euo pipefail

APP_USER="${MYSQL_USER:-zhishu}"

mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<-EOSQL
    CREATE USER IF NOT EXISTS '${APP_USER}'@'%' IDENTIFIED BY '${MYSQL_PASSWORD}';
    GRANT ALL PRIVILEGES ON meta.* TO '${APP_USER}'@'%';
    GRANT ALL PRIVILEGES ON dw.*   TO '${APP_USER}'@'%';
    FLUSH PRIVILEGES;
EOSQL

echo "[zz-grant.sh] granted meta/dw to '${APP_USER}'@'%'"
