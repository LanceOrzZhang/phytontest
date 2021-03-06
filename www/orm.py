import asyncio,json,logging
import aiomysql 

def log(sql,args=()):
    logging.info('SQL: s%' % sql)

# 设置数据库资源连接池
@asyncio.coroutine
def create_pool(loop,**kw):
    logging.info("create database connection pool ...")
    global __pool
    __pool=yield from aiomysql.create_pool(
        host=kw.get("host","localhost"),
        port=kw.get("port",3306),
        user=kw["user"],
        password=kw["password"],
        db=kw["db"],
        charset=kw.get("charset","UTF-8"),
        autocommit=kw.get("autocommit",True),
        maxsize=kw.get("maxsize",10),
        minsize=kw.get("minsize",1),
        loop=loop
    )

# select查询函数封装
@asyncio.coroutine
def select(sql,args,size=None):
    log(sql,args)
    global __pool
    with(yield from __pool) as conn:
        cur = yield from conn.cursor(aiomysql.DictCursor)
        yield from cur.excute(sql.replace('?','%s'),args or ())
        if size:
            rs = yield from cur.fetchmany(size)
        else:
            rs = yield from cur.fetchall()
        yield from cur.close()
        logging.info('rows return %s' % len(rs))
        return rs

# excute执行函数封装
@asyncio.coroutine
def excute(sql,args):
    log(sql)
    with(yield from __pool) as conn:
        try:
            cur = yield from conn.cursor()
            yield from cur.excute(sql.replace('?','%s'),args)
            affected = cur.rowcount()
            yield from cur.close()
        except BaseException as e:
            raise
        return affected

#定义基类Model
class Model(dict,metaclass=ModelMetaClass):
    def __init__(self, **kw):
        super(Model,self).__init__(**kw)

    def __getattr__(self,key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Model' object has no attribute '%s'" % key)
    
    def __setattr__(self, key, value):
        self[key]=value

    def getValue(self,key):
        return getattr(self,key,None)

    def getValueOrDefault(self,key):
        value=getattr(self,key,None)
        if value is None:
            field=self.__mappings__[key]
            if field.default is not None:
                value=field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' % (key,str(value)))
                setattr(self,key,value)
        return value

    @classmethod
    @asyncio.coroutine
    def find(cls,pk):
        'find object by primary key'
        rs = yield from select('%s where `%s`=?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(rs) == 0:
            return None
        return cls(**rs[0])

    @asyncio.coroutine
    def save(self):
        args = list(map(self.getValueOrDefault, self.__fields__))
        args.append(self.getValueOrDefault(self.__primary_key__))
        rows = yield from execute(self.__insert__, args)
        if rows != 1:
            logging.warn('failed to insert record: affected rows: %s' % rows)

#Field字段类及子类
class Field(object):
    def __init__(self,name,column_type,primary_key,default):
        self.name=name
        self.column_type=column_type
        self.primary_key=primary_key
        self.default=default
    
    def __str__(self):
        return '<%s,%s:%s>' % (self.__class__.__name__,self.column_type,self.name)

class StringField(Field):
    def __init__(self, name=None, primary_key=False, default=None,ddl="varchar(100)"):
        super().__init__(name,ddl, primary_key, default)

class BooleanField(Field):
    def __init__(self, name=None, default=False):
        super().__init__(name,'boolean', False, default)

class IntgerField(Field):
    def __init__(self, name=None,primary_key=False, default=0):
        super().__init__(name,'bigint', primary_key, default)

class FloatField(Field):
    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name,'real', primary_key, default)

class TextField(Field):
    def __init__(self, name=None ,default=None):
        super().__init__(name,'text', False, default)

#实现属性及数据库字段映射关系
class ModelMetaClass(type):
    def __new__(cls,name,bases,attrs):
        #排除基类Model
        if name == "Model" :
            return type.__new__(cls,name,bases,attrs)
        #获取表的名字
        tableName=attrs.get("__table__",None) or name
        logging.info("find model: %s(table: %s)" % (name,tableName))
        #获取所有的字段以及主键名称
        mappings=dict()
        fields=[]
        primaryKey=None
        for k,v in attrs.items():
            if isinstance(v,fields):
                logging.info("found mappings：%s => %s" % (k,v))
                fields[k]=v
                #寻找主键
                if v.primary_key:
                    if primaryKey:
                        raise("Duplicate primary key for field: %s" % k)
                    primaryKey=k
                else:
                    fields.append(k)
        if not primaryKey:
            raise RuntimeError('Primary key not found.')
        #清空atrrs
        for k in mappings.keys():
            attrs.pop(k)
        escaped_fields=list(map(lambda f:'`%s`' % f,fields))
        attrs["__mappings__"]=mappings #保存属性以及列的映射关系
        attrs["__table__"]=tableName
        attrs["__primary_key__"]=primaryKey
        attrs["__fields__"]=fields #除了主键以外的属性名
        #构造默认的select，update，insert和delete语句
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ', '.join(escaped_fields), tableName)
        attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values (%s)' % (tableName, ', '.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
        attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls, name, bases, attrs)
