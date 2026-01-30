FROM python:3.13-slim
ARG XDG_CACHE_DIR=/tmp/cache
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=${XDG_CACHE_DIR} \
    export PIP_CACHE_DIR=$XDG_CACHE_DIR/pip \
 && pip install -r /tmp/requirements.txt
COPY app /srv/app
WORKDIR /srv/
USER 1000
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host=0.0.0.0", "--log-level=debug"]
