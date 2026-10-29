#!/usr/bin/env bash
# 教育版 dev 后端：PostgreSQL 业务库 + Django dev server (8082)
# 前端另开终端：cd web && NODE_ENV=development BUILD_NO_SERVER=true \
#   DJANGO_HOSTNAME=http://localhost:8082 ./node_modules/.bin/nx run labelstudio:serve:development --host=0.0.0.0
# 注意：POSTGRE_* 必须通过进程环境传入——manage.py 读 .env 的时机晚于数据库配置。
set -e
cd "$(dirname "$0")/.."

export LABEL_STUDIO_BASE_DATA_DIR=/root/.local/share/label-studio-edu
export DJANGO_DB=default
export POSTGRE_HOST=127.0.0.1
export POSTGRE_PORT=5432
export POSTGRE_USER=postgres
export POSTGRE_PASSWORD=postgres
export POSTGRE_NAME=labelstudio_edu_pg
export CSRF_TRUSTED_ORIGINS=http://localhost:8010,http://localhost:8082,http://192.168.1.17:8010,http://192.168.1.17:8082

exec python label_studio/manage.py runserver 0.0.0.0:8082 --noreload
