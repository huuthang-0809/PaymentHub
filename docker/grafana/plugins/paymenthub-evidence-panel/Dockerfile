ARG DOCKER_REGISTRY=jfrog.vinsmartfuture.tech/vsf-shared-docker-virtual

FROM ${DOCKER_REGISTRY}/library/node:22-alpine AS build
WORKDIR /plugin
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY tsconfig.json webpack.config.cjs ./
COPY src ./src
RUN npm run build

FROM ${DOCKER_REGISTRY}/grafana/grafana:13.1.6
COPY --from=build /plugin/dist /opt/paymenthub-evidence-panel
COPY --chmod=755 install-panel.sh /usr/local/bin/paymenthub-grafana.sh
USER 472
ENTRYPOINT ["/usr/local/bin/paymenthub-grafana.sh"]
