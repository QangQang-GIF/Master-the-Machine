# arena-cobol:1 - GnuCOBOL 3.2 + python3 + git + bash for the COBOL suite.
# GnuCOBOL is built from the official GNU release (the Debian package of 3.2
# prints a libxml2 version warning on every program start).
FROM debian:bookworm-slim AS build
ARG GNUCOBOL_VERSION=3.2
ARG GNUCOBOL_SHA256=3bb48af46ced4779facf41fdc2ee60e4ccb86eaa99d010b36685315df39c2ee2
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates curl xz-utils \
      libgmp-dev libdb-dev libncurses-dev \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/gnucobol.tar.xz https://ftp.gnu.org/gnu/gnucobol/gnucobol-${GNUCOBOL_VERSION}.tar.xz \
 && echo "${GNUCOBOL_SHA256}  /tmp/gnucobol.tar.xz" | sha256sum -c - \
 && mkdir /tmp/src && tar -xJf /tmp/gnucobol.tar.xz -C /tmp/src --strip-components=1 \
 && cd /tmp/src \
 && ./configure --prefix=/usr/local --with-db --without-xml2 --without-json \
 && make -j"$(nproc)" \
 && make install \
 && rm -rf /tmp/src /tmp/gnucobol.tar.xz

FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev libgmp-dev libdb5.3 libncursesw6 \
      python3 git bash diffutils ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system user.email "arena@localhost" \
 && git config --system user.name "arena" \
 && git config --system init.defaultBranch main \
 && git config --system --add safe.directory '*'
COPY --from=build /usr/local /usr/local
RUN ldconfig && cobc --version | head -1 && mkdir -p /app /tests /logs/verifier
WORKDIR /app
CMD ["bash"]
