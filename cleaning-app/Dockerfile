FROM python:3.13-slim
WORKDIR /app
COPY server.py ./
COPY public ./public
ENV DB_PATH=/data/chores.db PORT=8080
VOLUME /data
EXPOSE 8080
CMD ["python3", "server.py"]
