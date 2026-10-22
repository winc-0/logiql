FROM elasticsearch:8.19.10

# IK 中文分词插件在构建时下载，不进仓库：
# 它是第三方二进制（4.5MB），提交进 git 只会让 clone 变慢，也容易被仓库体积策略盯上。
#
# 官方分发地址（旧文档里的 GitHub Releases 链接已停用，会 404）：
#   https://get.infini.cloud/elasticsearch/analysis-ik/<版本号>
# 若构建机无外网，把 zip 放到 docker/elasticsearch/plugins/ 下，
# 然后改用下面注释里的 COPY 方案（plugins/*.zip 已在 .gitignore 里）。
ARG IK_VERSION=8.19.10

USER root

RUN /usr/share/elasticsearch/bin/elasticsearch-plugin install --batch \
      "https://get.infini.cloud/elasticsearch/analysis-ik/${IK_VERSION}"

# --- 离线构建方案：先手动把 zip 放到 docker/elasticsearch/plugins/，再解除这两行注释 ---
# COPY plugins/elasticsearch-analysis-ik-8.19.10.zip /tmp/
# RUN /usr/share/elasticsearch/bin/elasticsearch-plugin install --batch \
#       file:///tmp/elasticsearch-analysis-ik-8.19.10.zip

RUN chown -R elasticsearch:elasticsearch /usr/share/elasticsearch/plugins

USER elasticsearch
