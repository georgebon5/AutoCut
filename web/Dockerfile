# ---------- deps ----------
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# npm ci fails without a lockfile; fall through to npm install if the
# repo is still on the first-commit, lockfile-less state.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi


# ---------- builder ----------
FROM node:20-alpine AS builder
WORKDIR /app

# API URL baked into the Next.js build. In docker-compose the service is
# called "api"; the rewrite in next.config.mjs turns /api/* → http://api:8000.
ARG AUTOCUT_API_URL=http://api:8000
ENV AUTOCUT_API_URL=${AUTOCUT_API_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build


# ---------- runner ----------
FROM node:20-alpine AS runner
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
WORKDIR /app

# .next/standalone would be nicer here but requires enabling `output: standalone`
# in next.config.mjs; copy the full build output for now to keep that config
# knob out of the way.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/next.config.mjs ./next.config.mjs

USER nextjs
EXPOSE 3000

CMD ["npm", "run", "start"]
