# Cram Sheet — Stephen Kerr, Round 2 (.NET / Energy Trading Platform)
Saved permanently at: ~/dev/code/hermes-v2/cram-sheets/dotnet-stephen-round2.md
v2 — deeper pass, built for a second-round technical/system-design conversation.

---

## 1. .NET 10 — go beyond "we're on LTS"

- **GC (DATAS)**: default since .NET 9, *tuned* in .NET 10 — fewer unnecessary
  collections, smoother pauses under high allocation rates, fixed fragmentation
  accounting that used to trigger extra gen1 collections. Say: "DATAS adapts heap
  count to app size automatically — good default for a service that scales to
  zero in Container Apps, since it won't over-allocate on cold start."
- **Native AOT**: JIT only runs at build time, nothing at runtime. Faster cold
  starts, smaller footprint. Real trade-off to name: AOT breaks reflection-heavy
  libraries and some DI patterns — you need source-generated serialization
  (`System.Text.Json` source generators) instead of runtime reflection. If they
  ask "would you AOT the whole platform" — no, AOT the edge/gateway services that
  need fast cold start under KEDA scale-to-zero; keep reflection-heavy internal
  services on standard JIT.
- **C# 14**: `field` keyword (auto-property backing field access without a full
  backing field), extension members. Minor, mention only if asked directly.

## 2. gRPC vs ZeroMQ — the question they'll actually probe

**Framing that shows seniority**: this isn't "which is faster," it's "what do you
give up for the speed."

| | gRPC | ZeroMQ |
|---|---|---|
| Transport | HTTP/2 | raw TCP/IPC, no HTTP overhead |
| Typing | Protobuf, compile-time contracts | none — you define the wire format |
| Security | TLS built in | you build it |
| Discovery/LB | built in (client or proxy-side) | you build it |
| Streaming | unary/server/client/bidi, native | pub-sub/push-pull patterns, manual |
| Throughput | ~3-10x faster than JSON, still HTTP/2 bound | fastest possible, no protocol tax |

**The real-world pattern to state**: gRPC at service boundaries (typed, secure,
observable — OpenTelemetry auto-instruments `HttpClient` and ASP.NET Core gRPC),
something lighter internally for the single hottest path — market-data tick
ingestion or order-execution — where every microsecond and every dependency
(TLS handshake, HTTP/2 framing) counts.

**Production detail that separates senior from mid**: gRPC's default max message
size is 4 MiB — fine for RPC, wrong tool if anyone suggests streaming large blobs
over it. For flow control on server-streaming, mention checking `isReady()` /
backpressure signals so a slow consumer doesn't force you to buffer in memory —
HTTP/2 flow control pauses the sender, but you should design for it explicitly,
not assume it's free.

**Sharper question than last time**: *"For the hot path specifically — are we
talking gRPC server-streaming for pushed ticks, or a broker (Kafka/Service Bus)
sitting in front so consumers can replay and fan out independently? That changes
whether we're solving a latency problem or a delivery-guarantee problem."*

## 3. Azure Container Apps — KEDA mechanics, not just "ACA vs AKS"

- ACA scaling **is** KEDA under the hood. Two scaler types matter for this role:
  - **HTTP scaling**: concurrent requests over a 15s window ÷ 15.
  - **TCP scaling**: use this, not HTTP, for gRPC services, WebSockets, and
    anything with long-lived connections or DB connection pools — say this
    explicitly, it's the correct answer if they probe "how would you scale the
    gRPC service."
- **Scale-to-zero cold start**: 2-10s depending on image size — this is exactly
  where Native AOT earns its keep (smaller image, faster cold start). Connect
  these two facts if asked "why would you use AOT here."
- **CPU/memory scaling cannot scale to zero** — only HTTP, TCP, and event-based
  (queue/Service Bus/Event Hubs/Kafka) scalers can. If the market-data path is
  queue-driven, that's your scale-to-zero story; if it's a persistent gRPC
  stream, min-replicas ≥ 1 is required — no cold start on a hot path.
- **ACA vs AKS, sharper than before**: ACA when you want microservices without
  owning the control plane; AKS when you need custom networking (service mesh,
  specific CNI), Windows containers, or fine-grained node control. For a mid-size
  trading platform, ACA is the likely fit *unless* they have strict co-location
  / network latency requirements between services, in which case AKS with node
  affinity gives more control.

## 4. Protobuf / FlatBuffers / Avro — with the "why" behind the numbers

- **Protobuf**: ~4.7M serializations/sec, pairs natively with gRPC, small
  payloads via varint encoding. Default choice.
- **FlatBuffers**: zero-copy — read fields directly off the buffer, no
  deserialize step, no allocation, no GC pressure. This is the one to name
  unprompted for the market-data hot path: "if we're parsing millions of ticks
  and can't afford GC pauses, FlatBuffers avoids allocating an object graph per
  message entirely."
- **Avro**: ~800K/sec, weakest on raw speed, strongest on schema evolution —
  standard in Kafka pipelines for downstream analytics, not for the hot path.
- One-liner: *"Protobuf for RPC contracts, FlatBuffers if we're parsing tick
  data with zero allocations, Avro if it's flowing through Kafka for downstream
  analytics."*

## 5. Resilience — likely round-2 addition, not covered last time

- **`Microsoft.Extensions.Resilience`** (built on `Polly.Core`, .NET 8+) is the
  current standard, configured via `AddResiliencePipeline` on
  `IHttpClientBuilder`. Know the five patterns cold:
  - **Retry** (exponential backoff + jitter — jitter matters, avoids thundering
    herd when many instances retry in sync)
  - **Circuit breaker** (stop calling a failing dependency, let it recover)
  - **Timeout** (don't let a slow call block a thread-pool thread)
  - **Bulkhead** (cap concurrent calls to one dependency so it can't starve the
    whole thread pool)
  - **Fallback** (cached/default response when all else fails)
- **Interview-grade point**: combine retry with circuit breaker — retrying into
  an already-open circuit wastes resources and delays failure detection.
- **Idempotency**: for anything that touches money or executes an order, every
  write endpoint needs an idempotency key. Say this unprompted if order
  execution comes up — it signals you've built payment/trading-adjacent systems
  before, not just read APIs.

## 6. System design — if this round is a design exercise

Framework to state upfront (shows structure): **clarify → capacity estimate →
API → data model → distribution → resilience → observability → trade-offs.**
Ask 4-6 sharp questions before drawing anything:
- reads/writes per second, peak vs average
- latency budget at p99 (not "fast" — a number)
- consistency requirement: strong (financial debits, order execution) vs
  eventual (analytics, notifications) — **name which parts of the platform need
  which**, that's the senior signal
- single region or multi-region
- what already exists (don't propose Kafka if Service Bus is already there)

**Default stance if asked "microservices or monolith"**: don't default to
microservices. "Start modular monolith unless team boundaries or independent
scaling targets justify the distributed-ops cost — for a trading platform I'd
keep the latency-critical path (pricing, execution) as few services as possible
and prove performance before splitting further." This is the answer that
signals seniority over "microservices are more scalable."

**Outbox pattern** — likely to come up for order/trade consistency: write the
domain change and an outbox record in the same DB transaction; a background
worker reads the outbox and publishes the event, marking it sent. Solves the
dual-write problem (DB commit succeeds, message publish fails, or vice versa)
without distributed transactions.

## 7. Two sharp questions to ask (up from one)

1. *"Is the low-latency requirement on market-data ingestion, order execution,
   or both — and is a message bus already in place or still being decided?"*
   (shows you separate the problem into distinct paths, not one blob)
2. *"For the consistency model — are trade executions strongly consistent by
   requirement, or is there tolerance for eventual consistency with a
   compensating-transaction/saga pattern if a downstream service is
   temporarily unavailable?"* (shows you think about failure modes before
   being asked)

---
*Researched fresh for round 2. Ask me to go deeper on any section — e.g. actual
gRPC + Polly code samples in C# — before the call.*
