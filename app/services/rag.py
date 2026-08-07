def retrieve_context(query: str) -> str:
    """
    Simulación de búsqueda RAG (Retriever-Augmented Generation).
    En producción, esto consultaría a ChromaDB usando SentenceTransformers.
    """
    return (
        "Políticas de facturación: Todo cobro por cambio de plan es proporcional "
        "a los días de uso (prorrateo). Si finaliza un descuento, el cargo fijo "
        "vuelve a su precio regular. La cuota de equipo se cobra por equipos financiados "
        "y tiene un número definido de cuotas."
    )
