FROM nginx:alpine

# Install curl for health checks
RUN apk add --no-cache curl

# Copy nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Copy static files
COPY dist/ /usr/share/nginx/html/

# Set proper permissions
RUN chmod -R 644 /usr/share/nginx/html/
RUN chmod 755 /usr/share/nginx/html/

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD curl -f http://localhost/health || exit 1

# Expose port
EXPOSE 80

# Use nginx's built-in signal handling
STOPSIGNAL SIGQUIT

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
