FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git cmake curl ffmpeg ca-certificates clang \
    && rm -rf /var/lib/apt/lists/*

ENV CC=clang CXX=clang++

WORKDIR /build

RUN git clone --depth 1 https://github.com/ggerganov/whisper.cpp.git . \
    && cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DWHISPER_BUILD_SERVER=ON \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_EXAMPLES=ON \
        -DBUILD_SHARED_LIBS=OFF \
    && cmake --build build --target whisper-server -j$(nproc) -- \
    && strip build/bin/whisper-server

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/build/bin/whisper-server /usr/local/bin/whisper-server

WORKDIR /models

RUN mkdir -p /tmp/whisper

EXPOSE 8090

CMD ["whisper-server", \
     "-m", "/models/ggml-base.en.bin", \
     "-l", "en", "--convert", "--tmp-dir", "/tmp/whisper", "-t", "4", \
     "--host", "0.0.0.0", "--port", "8090"]