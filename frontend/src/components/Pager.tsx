import { Button } from "@/components/ui/button";

type Props = {
  page: number;
  pageSize: number;
  /** Rows on this page: a full page means there may be another one. */
  shown: number;
  onPage: (page: number) => void;
};

/** Offset pages: the API gives no total, so «дальше» is offered while a page comes full. */
export function Pager({ page, pageSize, shown, onPage }: Props) {
  const first = (page - 1) * pageSize + 1;
  return (
    <div className="mt-3 flex items-center gap-2 text-sm">
      <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        ← Назад
      </Button>
      <span className="text-muted-foreground">
        {shown ? `Строки ${first}–${first + shown - 1}` : "Строк нет"}, страница {page}
      </span>
      <Button variant="outline" size="sm" disabled={shown < pageSize} onClick={() => onPage(page + 1)}>
        Дальше →
      </Button>
    </div>
  );
}
