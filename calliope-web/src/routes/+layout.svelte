<script lang="ts">
	import './app.css';
	import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
	import ToastHost from '$lib/components/ToastHost.svelte';
	import AuthGate from '$lib/components/AuthGate.svelte';
	import { auth } from '$lib/auth.svelte';
	import type { Snippet } from 'svelte';
	let { children }: { children: Snippet } = $props();

	const queryClient = new QueryClient({
		defaultOptions: {
			queries: { refetchOnWindowFocus: false, retry: 1 },
		},
	});
	$effect(() => {
		if (auth.enabled && !auth.user) queryClient.clear();
	});
</script>

<QueryClientProvider client={queryClient}>
	<AuthGate>{@render children()}</AuthGate>
	<ToastHost />
</QueryClientProvider>
