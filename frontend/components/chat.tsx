"use client";

import { useEffect, useRef, useState } from "react";
import {
  Bot,
  FileText,
  Globe,
  Loader2,
  Search,
  Send,
  TrendingUp,
  User,
} from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { toolLabel } from "@/lib/messages";
import { sendChatMessage, ChatApiError } from "@/lib/api";

interface ChatMessage {
  id: string;
  role: "human" | "ai";
  text: string;
  toolsUsed?: string[];
}

const SUGGESTIONS = [
  "Is there any insider selling this week?",
  "Has any customer concentration risk been disclosed recently?",
  "What's the latest news, and does it affect this position?",
];

function toolIcon(name?: string) {
  if (name === "search_filings" || name === "search_filings_exact") {
    return <FileText className="size-3" />;
  }
  if (name === "search_live_news") return <Globe className="size-3" />;
  if (name === "get_market_data") return <TrendingUp className="size-3" />;
  return <Search className="size-3" />;
}

export function Chat({ ticker, threadId }: { ticker: string; threadId: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isLoading]);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || isLoading) return;

    setError(null);
    setMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "human", text: question },
    ]);
    setInput("");
    setIsLoading(true);

    try {
      const result = await sendChatMessage({ ticker, question, threadId });
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "ai",
          text: result.answer,
          toolsUsed: result.tools_used,
        },
      ]);
    } catch (err) {
      setError(
        err instanceof ChatApiError
          ? err.message
          : "Something went wrong -- is the backend running?"
      );
    } finally {
      setIsLoading(false);
    }
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    send(input);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6">
          {messages.length === 0 && (
            // No repeated avatar/heading block here -- the page-level "Ask
            // North" header (page.tsx / portfolio's page.tsx) already
            // covers that; this panel goes straight from header to
            // suggestions, matching the reference design's minimal panel
            // rather than duplicating the heading a second time.
            <div className="mt-1 flex w-full flex-col gap-2">
              {/* Stacked, full-width, wrapped text -- not flex-wrap pills.
                  The Button component's variants force whitespace-nowrap
                  (see components/ui/button.tsx's buttonVariants), which
                  cuts text off rather than wrapping it once the column is
                  narrow (this chat panel is ~32% width, not the full
                  page) -- overridden here with whitespace-normal + h-auto
                  so multi-line suggestion text actually displays in full. */}
              {SUGGESTIONS.map((s) => (
                <Button
                  key={s}
                  variant="outline"
                  size="sm"
                  onClick={() => send(s)}
                  className="h-auto w-full justify-start text-left whitespace-normal"
                >
                  {s}
                </Button>
              ))}
            </div>
          )}

          {messages.map((message) => (
            <MessageRow key={message.id} message={message} />
          ))}

          {isLoading && <ThinkingRow />}

          {error != null && (
            <Card className="border-destructive/40">
              <CardContent className="text-sm text-destructive">
                {error}
              </CardContent>
            </Card>
          )}

          <div ref={endRef} />
        </div>
      </ScrollArea>

      <div className="border-t bg-background">
        <form
          onSubmit={onSubmit}
          className="mx-auto flex w-full max-w-3xl items-center gap-2 px-4 py-3"
        >
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={`Ask about ${ticker}...`}
            disabled={isLoading}
            className="h-10"
            autoFocus
          />
          <Button
            type="submit"
            size="lg"
            disabled={isLoading || input.trim().length === 0}
            className="h-10"
          >
            {isLoading ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Send className="size-4" />
            )}
          </Button>
        </form>
      </div>
    </div>
  );
}

function MessageRow({ message }: { message: ChatMessage }) {
  const isHuman = message.role === "human";

  return (
    <div
      className={cn(
        "flex w-full items-start gap-3",
        isHuman && "flex-row-reverse"
      )}
    >
      <Avatar>
        <AvatarFallback>
          {isHuman ? <User className="size-4" /> : <Bot className="size-4" />}
        </AvatarFallback>
      </Avatar>

      <div className={cn("flex max-w-[80%] flex-col gap-2", isHuman && "items-end")}>
        {message.toolsUsed && message.toolsUsed.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.toolsUsed.map((name) => (
              <Badge key={name} variant="secondary">
                {toolIcon(name)}
                {toolLabel(name)}
              </Badge>
            ))}
          </div>
        )}

        <div
          className={cn(
            "rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap",
            isHuman
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-foreground"
          )}
        >
          {message.text}
        </div>
      </div>
    </div>
  );
}

function ThinkingRow() {
  return (
    <div className="flex w-full items-start gap-3">
      <Avatar>
        <AvatarFallback>
          <Bot className="size-4" />
        </AvatarFallback>
      </Avatar>
      <div className="flex items-center gap-2 rounded-2xl bg-muted px-4 py-3 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Checking filings, news, and market data...
      </div>
    </div>
  );
}
