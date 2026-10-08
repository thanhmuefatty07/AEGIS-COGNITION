export function findModelForConnectionAndId(models, connectionId, modelId) {
  if (typeof connectionId !== "string" || connectionId.length === 0) return undefined;
  if (typeof modelId !== "string" || modelId.length === 0) return undefined;
  return models.find((model) => model.connection_id === connectionId && model.model_id === modelId);
}
