FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Files synced from OneDrive arrive mode 600; nginx runs unprivileged and needs to read them
RUN npm run build && chmod -R a+rX dist

# Unprivileged nginx: runs as a non-root user and listens on 8080
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --chmod=644 nginx.conf /etc/nginx/conf.d/default.conf
COPY --chmod=644 security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
