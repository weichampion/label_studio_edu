# Point cloud detection + tracking ML backend for Label Studio (built-in cluster detector, no dependencies).
# For a learned detector build on a CUDA/PyTorch image with OpenPCDet instead, see README.md.
FROM python:3.11-slim
WORKDIR /app
COPY tracker3d ./tracker3d
COPY openpcdet ./openpcdet
COPY _wsgi.py .
# trained models, one folder per Label Studio project: mount a volume here to keep them
RUN mkdir -p /data/models && chown nobody /data/models
ENV PORT=9090 PYTHONUNBUFFERED=1 MODEL_DIR=/data/models
VOLUME /data/models
EXPOSE 9090
USER nobody
CMD ["python", "_wsgi.py"]
