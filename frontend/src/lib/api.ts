/** The generated client answers `{ data, error, response }` instead of throwing; react-query
 * needs a thrown error to show one. */
export class ApiError extends Error {
  readonly status: number | undefined;

  constructor(message: string, status: number | undefined) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type ClientResult<T> = {
  data?: T;
  error?: unknown;
  response?: Response;
};

/** The API's own words: FastAPI puts them in `detail`, a string or a list of problems;
 * the app's own errors (a refused cross-origin request, a crash) in `error.message`. */
export function errorMessage(error: unknown, status: number | undefined): string {
  if (error && typeof error === "object" && "error" in error) {
    const body = (error as { error: unknown }).error;
    if (body && typeof body === "object" && "message" in body && typeof body.message === "string") {
      return body.message;
    }
  }
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : JSON.stringify(item)))
        .join("; ");
    }
    return JSON.stringify(detail);
  }
  if (typeof error === "string" && error) {
    return error;
  }
  return status ? `Сервер ответил ${status}` : "Сервер не ответил";
}

export async function unwrap<T>(request: Promise<ClientResult<T>>): Promise<T> {
  const result = await request;
  if (result.error !== undefined || result.data === undefined) {
    const status = result.response?.status;
    throw new ApiError(errorMessage(result.error, status), status);
  }
  return result.data;
}
