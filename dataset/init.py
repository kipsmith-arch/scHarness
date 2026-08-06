'''
处理刚下载的H5AD
只保留稀疏矩阵删掉稠密部分
处理版本区别
'''
import os
import scanpy as sc
import anndata as ad
from glob import glob

def read_h5ad(path:str) -> sc.AnnData:
    """
    Read an h5ad file.

    Args:
        path: Path to the h5ad file.

    Returns:
        AnnData object.
    """
    adata = sc.read_h5ad(path)
    raw_available = hasattr(adata, 'raw') and adata.raw is not None
    
    # the old version of scanpy, rebuild the raw
    if raw_available and '_index' in adata.raw.var.columns:
        raw_var = adata.raw.var.set_index('_index').copy()
        raw_var.index = raw_var.index.astype(str)
        raw_var.index.name = None
        adata.raw = ad.AnnData(X=adata.raw.X, var=raw_var)
        print("it's saved by old version of scanpy, rebuild the raw.")

    # check gene names
    if raw_available:
        try:
            adata.raw.var_names.astype(float)# if it's gene names, it will raise a ValueError
            raise TypeError('adata.raw.var_names isn\'t gene names!')
        except ValueError:
            pass
    if not raw_available:
        try:
            adata.var_names.astype(float)# if it's gene names, it will raise a ValueError
            raise TypeError('adata.var_names isn\'t gene names, cann\'t find available gene names!')
        except ValueError:
            pass

    return adata

for h5ad_file in glob('h5ad/*.h5ad'):
    print(h5ad_file)
    adata = read_h5ad(h5ad_file)
    adata.obs[['Seurat_clusters','Celltype']].to_csv('index/'+os.path.basename(h5ad_file)+'.csv')
    del adata.obs['Seurat_clusters']
    del adata.obs['Celltype']
    adata.write(h5ad_file)

