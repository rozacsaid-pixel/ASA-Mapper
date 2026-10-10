FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY asa_mapper ./asa_mapper
ENV ASA_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "asa_mapper.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
