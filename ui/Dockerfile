# UI Image serving static chat web interface
FROM nginxinc/nginx-unprivileged:1.27-alpine

# Copy nginx config configured for non-root and port 8080
COPY nginx.conf /etc/nginx/nginx.conf

# Copy chat interface HTML
COPY index.html /usr/share/nginx/html/index.html

USER 101

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
