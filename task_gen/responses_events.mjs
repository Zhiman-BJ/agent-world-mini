// Adapt a complete Responses JSON object to the SDK's streaming event interface.
// These are local events, never represented as original upstream SSE.
export function responseEvents(response) {
  if (!['completed', 'incomplete', 'failed'].includes(response.status) || !Array.isArray(response.output)) {
    throw new Error('Invalid non-streaming Responses result');
  }
  const events = [];
  const emit = (type, data) => events.push({ type, sequence_number: events.length, ...data });
  emit('response.created', { response: { ...response, output: [], status: 'in_progress' } });
  response.output.forEach((item, output_index) => {
    const common = { item_id: item.id, output_index };
    if (item.type === 'function_call') {
      if (typeof item.arguments !== 'string') throw new Error('Missing function arguments');
      JSON.parse(item.arguments);
      emit('response.output_item.added', { output_index, item: { ...item, arguments: '' } });
      emit('response.function_call_arguments.delta', { ...common, delta: item.arguments });
      emit('response.function_call_arguments.done', { ...common, arguments: item.arguments });
    } else if (item.type === 'reasoning') {
      emit('response.output_item.added', { output_index, item });
      for (const [summary_index, part] of (item.summary ?? []).entries()) {
        if (part.type !== 'summary_text') throw new Error('Unsupported reasoning summary');
        emit('response.reasoning_summary_part.added', { ...common, summary_index, part: { type: 'summary_text', text: '' } });
        emit('response.reasoning_summary_text.delta', { ...common, summary_index, delta: part.text });
      }
      if (item.content?.length) throw new Error('Unsupported reasoning content; raw response retained');
    } else if (item.type === 'message') {
      emit('response.output_item.added', { output_index, item: { ...item, content: [] } });
      for (const [content_index, part] of item.content.entries()) {
        if (part.type !== 'output_text') throw new Error('Unsupported Responses message content');
        emit('response.content_part.added', { ...common, content_index, part: { ...part, text: '' } });
        emit('response.output_text.delta', { ...common, content_index, delta: part.text });
        emit('response.output_text.done', { ...common, content_index, text: part.text });
        emit('response.content_part.done', { ...common, content_index, part });
      }
    } else {
      throw new Error(`Unsupported Responses output item: ${item.type}`);
    }
    emit('response.output_item.done', { output_index, item });
  });
  emit(`response.${response.status}`, { response });
  return events;
}
