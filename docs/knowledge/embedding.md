
embedding 用 bi-encoder：把文档切块，每块转成一个向量
问答时， 把问题（query）转成向量，用 bi-encoder 计算 query 向量和文档向量间的相关性
这个过程 query 向量和文档向量都是分别计算的。文档向量是在文档入库时就算好了，所以召回速度快，但精度低。
召回top_k：就是把相关性最高 k 个文档块返回

cross-encoder 则是要即时算的，要把 (query, doc) 成对输入计算相关性，精度更高。
运行不可能把 query 和 kb 里所有doc chunk 算一遍，所以时基于 top_k * N 来算。
然后把cross-encoder 算的 top_k * N 进行排序，取出 top_k 作为最后结果。
这个过程就是 re-rank(二次精排)。

