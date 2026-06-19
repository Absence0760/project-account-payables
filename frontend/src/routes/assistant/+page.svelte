<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { api, streamAssistantChat, AssistantBudgetError } from '$lib/api';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import ChatMessage from '$lib/components/assistant/ChatMessage.svelte';
	import ExamplePrompts from '$lib/components/assistant/ExamplePrompts.svelte';
	import UsageMeter from '$lib/components/assistant/UsageMeter.svelte';
	import type {
		ChatResponse,
		ConversationDetail,
		ConversationListResponse,
		ConversationSummary,
		ToolInvocation,
		UiMessage,
		UsageResponse
	} from '$lib/types/assistant';

	let messages = $state<UiMessage[]>([]);
	let input = $state('');
	let busy = $state(false);
	let conversationId = $state<string | null>(null);
	let usage = $state<UsageResponse | null>(null);
	let conversations = $state<ConversationSummary[]>([]);
	let budgetNotice = $state<string | null>(null);
	let scrollEl = $state<HTMLDivElement | null>(null);

	let isEmpty = $derived(messages.length === 0);

	async function loadUsage() {
		try {
			usage = await api.get<UsageResponse>('/api/assistant/usage');
		} catch {
			// Usage is advisory — a failure here must not block chatting.
			usage = null;
		}
	}

	async function loadConversations() {
		try {
			const res = await api.get<ConversationListResponse>('/api/assistant/conversations');
			conversations = res.items;
		} catch {
			conversations = [];
		}
	}

	onMount(() => {
		void loadUsage();
		void loadConversations();
	});

	async function scrollToBottom() {
		await tick();
		if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight;
	}

	function newChat() {
		conversationId = null;
		messages = [];
		budgetNotice = null;
		input = '';
	}

	async function openConversation(id: string) {
		if (busy) return;
		try {
			const detail = await api.get<ConversationDetail>(`/api/assistant/conversations/${id}`);
			conversationId = detail.conversation.id;
			messages = detail.messages
				.filter((m) => m.role === 'user' || m.role === 'assistant')
				.map((m) => ({
					role: m.role === 'user' ? 'user' : 'assistant',
					content: m.content,
					tools: m.tool_calls ?? [],
					streaming: false
				}));
			budgetNotice = null;
			await scrollToBottom();
		} catch {
			budgetNotice = 'Could not load that conversation.';
		}
	}

	function pickPrompt(prompt: string) {
		input = prompt;
		void send();
	}

	/** Apply the authoritative `done`/`/chat` payload to the in-progress
	 *  assistant message and refresh side state. */
	function applyFinal(payload: ChatResponse, assistantIdx: number) {
		conversationId = payload.conversation_id;
		const msg = messages[assistantIdx];
		if (msg) {
			msg.content = payload.answer;
			msg.tools = (payload.tool_invocations as ToolInvocation[]) ?? msg.tools;
			msg.streaming = false;
		}
	}

	/** Non-streaming fallback — used when the stream endpoint is unavailable
	 *  (404 during local dev) or fails mid-flight before any content arrived. */
	async function fallbackChat(message: string, assistantIdx: number) {
		const payload = await api.post<ChatResponse>('/api/assistant/chat', {
			message,
			conversation_id: conversationId ?? undefined
		});
		applyFinal(payload, assistantIdx);
	}

	async function send() {
		const message = input.trim();
		if (!message || busy) return;
		busy = true;
		budgetNotice = null;
		input = '';

		messages.push({ role: 'user', content: message, tools: [] });
		const assistantIdx = messages.push({
			role: 'assistant',
			content: '',
			tools: [],
			streaming: true
		}) - 1;
		await scrollToBottom();

		let sawContent = false;
		try {
			await streamAssistantChat(
				{ message, conversation_id: conversationId ?? undefined },
				{
					onTool: (frame) => {
						const msg = messages[assistantIdx];
						if (!msg) return;
						msg.tools = [
							...msg.tools,
							{
								tool: frame.tool,
								args: frame.args ?? {},
								result: frame.result ?? null,
								error: frame.error ?? null
							}
						];
						sawContent = true;
						void scrollToBottom();
					},
					onDelta: (text) => {
						const msg = messages[assistantIdx];
						if (!msg) return;
						msg.content += text;
						sawContent = true;
						void scrollToBottom();
					},
					onDone: (payload) => {
						applyFinal(payload as ChatResponse, assistantIdx);
						sawContent = true;
					},
					onError: (frame) => {
						const msg = messages[assistantIdx];
						if (msg && !sawContent) {
							msg.error = frame.detail || 'The assistant hit an error.';
							msg.streaming = false;
						}
					}
				}
			);
		} catch (err) {
			if (err instanceof AssistantBudgetError) {
				budgetNotice = `Monthly AI budget reached (${err.used.toLocaleString()} / ${err.budget.toLocaleString()} tokens). Resets next period.`;
				// Drop the empty in-progress assistant bubble.
				messages.splice(assistantIdx, 1);
			} else if (!sawContent) {
				// Stream unavailable / failed before any content → non-streaming
				// fallback so the page works against `/chat` alone.
				try {
					await fallbackChat(message, assistantIdx);
				} catch (fallbackErr) {
					if (fallbackErr instanceof AssistantBudgetError) {
						budgetNotice = `Monthly AI budget reached (${fallbackErr.used.toLocaleString()} / ${fallbackErr.budget.toLocaleString()} tokens). Resets next period.`;
						messages.splice(assistantIdx, 1);
					} else {
						const msg = messages[assistantIdx];
						const detail =
							fallbackErr instanceof Error ? fallbackErr.message : 'Something went wrong.';
						if (msg) {
							msg.error = detail.includes('budget')
								? detail
								: `The assistant could not answer: ${detail}`;
							msg.streaming = false;
						}
					}
				}
			}
		} finally {
			const msg = messages[assistantIdx];
			if (msg) msg.streaming = false;
			busy = false;
			void loadUsage();
			void loadConversations();
			void scrollToBottom();
		}
	}

	function onKeydown(e: KeyboardEvent) {
		// Enter sends; Shift+Enter inserts a newline.
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			void send();
		}
	}
</script>

<PageHeader title="AI Assistant">
	{#snippet actions()}
		<button type="button" class="btn-secondary" onclick={newChat} disabled={busy}>+ New chat</button>
	{/snippet}

	<div class="assistant-layout">
		<aside class="convo-rail">
			<UsageMeter {usage} />
			<div class="convo-head">Recent</div>
			{#if conversations.length === 0}
				<p class="convo-empty">No conversations yet.</p>
			{:else}
				<ul class="convo-list">
					{#each conversations as c (c.id)}
						<li>
							<button
								type="button"
								class="convo-item"
								class:active={c.id === conversationId}
								onclick={() => openConversation(c.id)}
								disabled={busy}
							>
								<span class="convo-title">{c.title || 'Untitled chat'}</span>
								<span class="convo-count">{c.message_count} msg</span>
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</aside>

		<section class="chat-pane">
			<div
				class="chat-scroll"
				bind:this={scrollEl}
				role="log"
				aria-live="polite"
				aria-busy={busy}
			>
				{#if isEmpty}
					<ExamplePrompts onpick={pickPrompt} />
				{:else}
					<div class="msg-stream">
						{#each messages as message, i (i)}
							<ChatMessage {message} />
						{/each}
					</div>
				{/if}
			</div>

			{#if budgetNotice}
				<div class="budget-notice" role="alert">{budgetNotice}</div>
			{/if}

			<form
				class="composer"
				onsubmit={(e) => {
					e.preventDefault();
					void send();
				}}
			>
				<textarea
					bind:value={input}
					onkeydown={onKeydown}
					placeholder="Ask about spend, approvals, forecasts, invoices…"
					rows="1"
					aria-label="Message the assistant"
					disabled={busy}
				></textarea>
				<button type="submit" class="btn-primary" disabled={busy || input.trim().length === 0}>
					{busy ? 'Thinking…' : 'Send'}
				</button>
			</form>
		</section>
	</div>
</PageHeader>

<style>
	.assistant-layout {
		display: grid;
		grid-template-columns: 240px 1fr;
		gap: 16px;
		flex: 1 1 auto;
		min-height: 0;
	}
	.convo-rail {
		display: flex;
		flex-direction: column;
		gap: 12px;
		padding: 14px;
		border: 1px solid var(--border);
		border-radius: 12px;
		background: var(--surface);
		height: fit-content;
		max-height: calc(100vh - 140px);
		overflow-y: auto;
	}
	.convo-head {
		font-size: 0.7rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-muted);
		opacity: 0.7;
		padding-top: 4px;
		border-top: 1px solid var(--border);
	}
	.convo-empty {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin: 0;
	}
	.convo-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.convo-item {
		width: 100%;
		text-align: left;
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 8px 10px;
		border-radius: 8px;
		border: 1px solid transparent;
		background: none;
		color: var(--text);
		cursor: pointer;
		font-family: inherit;
	}
	.convo-item:hover {
		background: rgba(99, 140, 255, 0.08);
	}
	.convo-item.active {
		border-color: var(--accent);
		background: rgba(99, 140, 255, 0.1);
	}
	.convo-title {
		font-size: 0.84rem;
		font-weight: 500;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.convo-count {
		font-size: 0.72rem;
		color: var(--text-muted);
	}
	.chat-pane {
		display: flex;
		flex-direction: column;
		gap: 12px;
		min-height: 0;
		border: 1px solid var(--border);
		border-radius: 12px;
		background: var(--bg, var(--surface));
		overflow: hidden;
	}
	.chat-scroll {
		flex: 1 1 auto;
		min-height: 420px;
		max-height: calc(100vh - 220px);
		overflow-y: auto;
		padding: 20px;
	}
	.msg-stream {
		display: flex;
		flex-direction: column;
		gap: 18px;
	}
	.budget-notice {
		margin: 0 16px;
		padding: 10px 14px;
		border-radius: 8px;
		background: rgba(240, 70, 70, 0.1);
		border: 1px solid rgba(240, 70, 70, 0.3);
		color: #e04040;
		font-size: 0.85rem;
	}
	.composer {
		display: flex;
		gap: 10px;
		align-items: flex-end;
		padding: 14px 16px;
		border-top: 1px solid var(--border);
	}
	.composer textarea {
		flex: 1 1 auto;
		resize: none;
		min-height: 42px;
		max-height: 160px;
		padding: 10px 14px;
		border-radius: 10px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-family: inherit;
		font-size: 0.92rem;
		line-height: 1.4;
	}
	.composer textarea:focus {
		outline: none;
		border-color: var(--accent);
	}
	.btn-secondary {
		padding: 8px 14px;
		border-radius: 8px;
		border: 1px solid var(--border);
		background: var(--surface);
		color: var(--text);
		font-family: inherit;
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
	}
	.btn-secondary:hover:not(:disabled) {
		border-color: var(--accent);
		color: var(--accent);
	}
	.btn-secondary:disabled,
	.composer .btn-primary:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.composer .btn-primary {
		align-self: stretch;
		padding: 0 22px;
	}

	@media (max-width: 720px) {
		.assistant-layout {
			grid-template-columns: 1fr;
		}
		.convo-rail {
			max-height: none;
		}
	}
</style>
