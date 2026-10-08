import type { MouseEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getWikiPageV1, listWikiV1 } from "@/api/generated";
import { PageHeader } from "@/components/layout/PageHeader";
import { QueryState } from "@/components/QueryState";
import { unwrap } from "@/lib/api";
import { cn } from "@/lib/utils";

/** The wiki: the pages on the left, one page as the server renders the repository's own
 * markdown (raw HTML in it is shown as text there, so the HTML is safe to put in). */
export function WikiPage() {
  const { slug = "Home" } = useParams();
  const navigate = useNavigate();
  const pages = useQuery({ queryKey: ["wiki"], queryFn: () => unwrap(listWikiV1()) });
  const page = useQuery({ queryKey: ["wiki", slug], queryFn: () => unwrap(getWikiPageV1({ path: { slug } })) });

  // A link to another wiki page opens it here, without reloading the console.
  function follow(event: MouseEvent<HTMLDivElement>) {
    const link = (event.target as HTMLElement).closest("a");
    const href = link?.getAttribute("href") ?? "";
    if (href.startsWith("/wiki/")) {
      event.preventDefault();
      navigate(href);
    }
  }

  return (
    <>
      <PageHeader title="Вики" instruction="Справочник по проекту, pipeline и операторской консоли." />
      <div className="grid items-start gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <nav aria-label="Страницы вики" className="space-y-1 text-sm lg:sticky lg:top-4">
          <a className="mb-2 block underline" href="/ui/wiki/export.pdf">
            Скачать вики в PDF
          </a>
          {(pages.data ?? []).map((item) => (
            <Link key={item.slug} to={`/wiki/${item.slug}`} className={cn("block rounded px-2 py-1", item.slug === slug ? "bg-primary text-primary-foreground" : "hover:bg-muted")}>
              {item.slug}
            </Link>
          ))}
        </nav>
        <QueryState query={page}>
          {(data) => (
            <article className="wiki rounded-lg border bg-card p-6" onClick={follow} dangerouslySetInnerHTML={{ __html: data.html }} />
          )}
        </QueryState>
      </div>
    </>
  );
}
