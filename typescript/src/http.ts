/**
 * Shared request executor of the Zelinqa V1 SDK.
 *
 * Zero runtime dependency: the global `fetch` is used unless the caller injects
 * one. The executor owns the cross-cutting concerns both clients need — headers,
 * idempotency keys, retries, per-attempt timeouts and error mapping — so that
 * `runtime.ts` and `configuration.ts` only describe routes.
 *
 * The API key is written to exactly one place, the `Authorization` header. It is
 * never logged, never copied into an error and never returned by any accessor.
 */
import { apiErrorFromResponse, ZelinqaAPIError, ZelinqaConnectionError } from "./errors.js";
import type { Fetch, RequestOptions, ZelinqaClientOptions } from "./types.js";
import { VERSION } from "./version.js";

export const DEFAULT_BASE_URL = "https://api.zelinqa.ai";
export const DEFAULT_TIMEOUT_MS = 30_000;
export const DEFAULT_MAX_RETRIES = 2;
export const USER_AGENT = `zelinqa-typescript/${VERSION}`;

const RETRIABLE_STATUS_CODES: ReadonlySet<number> = new Set([429, 500, 502, 503, 504]);
const BACKOFF_BASE_MS = 500;
const BACKOFF_CAP_MS = 10_000;
const BACKOFF_JITTER_RATIO = 0.25;
const RETRY_AFTER_CAP_MS = 30_000;
const MIN_IDEMPOTENCY_KEY_CHARS = 8;
const MAX_IDEMPOTENCY_KEY_CHARS = 128;
const MAX_PATH_SEGMENT_CHARS = 128;

const TIMEOUT_REASON = Symbol("zelinqa.attempt.timeout");

export type QueryValue = string | number | boolean | undefined;
export type QueryParams = Readonly<Record<string, QueryValue>>;

export interface HttpRequest {
  readonly method: "GET" | "POST";
  /** Absolute route path, starting with `/`. */
  readonly path: string;
  readonly query?: QueryParams | undefined;
  /** JSON body. Omit for `GET`; `POST` routes always send a body. */
  readonly body?: unknown;
  /** True when the route is a mutation and must carry `Idempotency-Key`. */
  readonly mutation?: boolean;
  /** `Accept` header. Defaults to `application/json`. */
  readonly accept?: string;
  readonly options?: RequestOptions | undefined;
}

/** Milliseconds to wait before retrying attempt `attempt` (0-based). */
export function backoffMs(attempt: number, retryAfterSeconds?: number | undefined): number {
  if (retryAfterSeconds !== undefined && Number.isFinite(retryAfterSeconds)) {
    return Math.min(Math.max(retryAfterSeconds, 0) * 1000, RETRY_AFTER_CAP_MS);
  }
  const base = Math.min(BACKOFF_BASE_MS * 2 ** attempt, BACKOFF_CAP_MS);
  return Math.min(base + Math.random() * base * BACKOFF_JITTER_RATIO, BACKOFF_CAP_MS);
}

function validateBaseUrl(rawBaseUrl: string): string {
  let parsed: URL;
  try {
    parsed = new URL(rawBaseUrl);
  } catch {
    throw new TypeError("baseUrl must be an absolute HTTP(S) URL");
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new TypeError("baseUrl must be an absolute HTTP(S) URL");
  }
  if (parsed.username || parsed.password) {
    throw new TypeError("baseUrl must not contain credentials");
  }
  if (parsed.search || parsed.hash) {
    throw new TypeError("baseUrl must not contain a query string or fragment");
  }
  return parsed.toString().replace(/\/$/, "");
}

/** Validates an opaque identifier used in a path and percent-encodes it. */
export function encodePathSegment(value: string, name: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new TypeError(`${name} must not be empty`);
  }
  if (value.length > MAX_PATH_SEGMENT_CHARS) {
    throw new TypeError(`${name} must not exceed ${MAX_PATH_SEGMENT_CHARS} characters`);
  }
  return encodeURIComponent(value);
}

function resolveIdempotencyKey(provided: string | undefined): string {
  if (provided === undefined) {
    return globalThis.crypto.randomUUID();
  }
  if (provided.trim() === "") {
    throw new TypeError("idempotencyKey must not be empty");
  }
  if (provided.length < MIN_IDEMPOTENCY_KEY_CHARS || provided.length > MAX_IDEMPOTENCY_KEY_CHARS) {
    throw new TypeError(
      `idempotencyKey must be between ${MIN_IDEMPOTENCY_KEY_CHARS} and ${MAX_IDEMPOTENCY_KEY_CHARS} characters`,
    );
  }
  return provided;
}

function buildQueryString(query: QueryParams | undefined): string {
  if (query === undefined) {
    return "";
  }
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined) {
      continue;
    }
    search.set(key, typeof value === "string" ? value : String(value));
  }
  const serialized = search.toString();
  return serialized === "" ? "" : `?${serialized}`;
}

async function readPayload(response: Response): Promise<unknown> {
  let text: string;
  try {
    text = await response.text();
  } catch {
    return undefined;
  }
  if (text.trim() === "") {
    return undefined;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

function sleep(milliseconds: number, signal: AbortSignal | undefined): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted === true) {
      reject(signal.reason);
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    function onAbort(): void {
      clearTimeout(timer);
      reject(signal?.reason);
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Executes Zelinqa requests: headers, idempotency, retries, timeouts, error mapping. */
export class HttpTransport {
  readonly #apiKey: string;
  readonly #baseUrl: string;
  readonly #timeoutMs: number;
  readonly #maxRetries: number;
  readonly #fetch: Fetch;

  public constructor(options: ZelinqaClientOptions) {
    if (typeof options?.apiKey !== "string" || options.apiKey.trim() === "") {
      throw new TypeError("apiKey must not be empty");
    }
    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      throw new TypeError("timeoutMs must be greater than zero");
    }
    const maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES;
    if (!Number.isInteger(maxRetries) || maxRetries < 0) {
      throw new TypeError("maxRetries must be a non-negative integer");
    }
    const fetchImplementation = options.fetch ?? globalThis.fetch;
    if (typeof fetchImplementation !== "function") {
      throw new TypeError("A Fetch API implementation is required");
    }

    this.#apiKey = options.apiKey;
    this.#baseUrl = validateBaseUrl(options.baseUrl ?? DEFAULT_BASE_URL);
    this.#timeoutMs = timeoutMs;
    this.#maxRetries = maxRetries;
    this.#fetch = fetchImplementation;
  }

  public get baseUrl(): string {
    return this.#baseUrl;
  }

  /** Never exposes the API key. */
  public toString(): string {
    return `HttpTransport(baseUrl=${this.#baseUrl})`;
  }

  public async requestJson<T>(request: HttpRequest): Promise<T> {
    const response = await this.#send(request);
    const payload = await readPayload(response);
    if (payload === undefined || typeof payload !== "object" || payload === null) {
      throw new ZelinqaAPIError("Zelinqa API returned a non-JSON response", {
        statusCode: response.status,
        requestId: response.headers.get("X-Request-Id") ?? undefined,
      });
    }
    return payload as T;
  }

  public async requestText(request: HttpRequest): Promise<string> {
    const response = await this.#send(request);
    return await response.text();
  }

  async #send(request: HttpRequest): Promise<Response> {
    const url = `${this.#baseUrl}${request.path}${buildQueryString(request.query)}`;
    const callerSignal = request.options?.signal;
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.#apiKey}`,
      Accept: request.accept ?? "application/json",
      "User-Agent": USER_AGENT,
    };
    if (request.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (request.mutation === true) {
      // Generated once per logical call and reused across every retry, so a retry
      // can never double a turn, an outcome, a feedback or a publication.
      headers["Idempotency-Key"] = resolveIdempotencyKey(request.options?.idempotencyKey);
    }
    const body = request.body === undefined ? undefined : JSON.stringify(request.body);

    for (let attempt = 0; ; attempt += 1) {
      let response: Response;
      try {
        response = await this.#attempt(url, request.method, headers, body, callerSignal);
      } catch (error) {
        if (callerSignal?.aborted === true) {
          throw error;
        }
        if (attempt >= this.#maxRetries) {
          throw new ZelinqaConnectionError("Unable to reach the Zelinqa API", { cause: error });
        }
        await sleep(backoffMs(attempt), callerSignal);
        continue;
      }

      if (response.ok) {
        return response;
      }

      const payload = await readPayload(response);
      const apiError = apiErrorFromResponse(response.status, payload, response.headers);
      if (!RETRIABLE_STATUS_CODES.has(response.status) || attempt >= this.#maxRetries) {
        throw apiError;
      }
      await sleep(backoffMs(attempt, apiError.retryAfter), callerSignal);
    }
  }

  async #attempt(
    url: string,
    method: "GET" | "POST",
    headers: Readonly<Record<string, string>>,
    body: string | undefined,
    callerSignal: AbortSignal | undefined,
  ): Promise<Response> {
    if (callerSignal?.aborted === true) {
      throw callerSignal.reason;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(TIMEOUT_REASON), this.#timeoutMs);
    const forwardAbort = (): void => {
      controller.abort(callerSignal?.reason);
    };
    callerSignal?.addEventListener("abort", forwardAbort, { once: true });
    try {
      return await this.#fetch(url, {
        method,
        headers: { ...headers },
        ...(body === undefined ? {} : { body }),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
      callerSignal?.removeEventListener("abort", forwardAbort);
    }
  }
}
