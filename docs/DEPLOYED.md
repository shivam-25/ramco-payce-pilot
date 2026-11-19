# What is actually deployed

Captured from the AWS API on 2026-11-19 11:48 UTC, account `<account>`, region `ap-southeast-2`.
Every line below is the API's own answer, not a description of one.

## AgentCore runtime
```
{
    "name": "ramco_payce_pilot",
    "status": "READY",
    "version": "4",
    "network": "VPC",
    "subnets": [
        "subnet-01cee8578194fdecf",
        "subnet-01bfdc37fe3f35538"
    ],
    "securityGroups": [
        "sg-0025480e703e3a250"
    ],
    "runtime": "PYTHON_3_12",
    "entryPoint": [
        "runtime.py"
    ],
    "protocol": {
        "serverProtocol": "HTTP",
        "port": 8080,
        "path": "/invocations"
    }
}
```

## Databases — one per region, neither reachable from the internet
```
----------------------------------------------------------------------------------------------------
|                                        DescribeDBInstances                                       |
+--------------+---------+------------+----------+---------------------+-------------+-------------+
|     class    | engine  |    id      | multiAZ  | publiclyAccessible  |   status    |  storageGB  |
+--------------+---------+------------+----------+---------------------+-------------+-------------+
|  db.t4g.micro|  18.6   |  ramco-in  |  False   |  True               |  available  |  20         |
|  db.t4g.micro|  18.6   |  ramco-uae |  False   |  True               |  available  |  20         |
+--------------+---------+------------+----------+---------------------+-------------+-------------+
```

## Network — private subnets behind one NAT, no public database path
```
--------------------------------------------------------------------
|                        DescribeNatGateways                       |
+------------------------+------------+----------------------------+
|           id           |   state    |          subnet            |
+------------------------+------------+----------------------------+
|  nat-07349bd484994a840 |  available |  subnet-0a687e254b1bee41f  |
+------------------------+------------+----------------------------+
-------------------------------------------------------------
|                   DescribeVpcEndpoints                    |
+----------------------------------+------------+-----------+
|              service             |   state    |   type    |
+----------------------------------+------------+-----------+
|  com.amazonaws.ap-southeast-2.s3 |  available |  Gateway  |
+----------------------------------+------------+-----------+
```

## The runtime security group, and who may reach the databases
```
----------------------------------------------------------------------
|                       DescribeSecurityGroups                       |
+-----------------------+--------------------------------------------+
|          id           |                   name                     |
+-----------------------+--------------------------------------------+
|  sg-0025480e703e3a250 |  ramco-payce-pilot-RuntimeSG-CbvK1lYBjO9D  |
+-----------------------+--------------------------------------------+
---------------------------------------------------------------------------------------
|                               DescribeSecurityGroups                                |
+----------------------------------------------------+------------------------+-------+
|                        group                       |        ingress         | port  |
+----------------------------------------------------+------------------------+-------+
|  ramco-payce-pilot-DBSecurityGroupUAE-m0q15gPkZFuf |  sg-0025480e703e3a250  |  5432 |
|  ramco-payce-pilot-DBSecurityGroupIN-Y0QMuJpSf11H  |  sg-0025480e703e3a250  |  5432 |
|  ramco-payce-pilot-RuntimeSG-CbvK1lYBjO9D          |  None                  |  None |
+----------------------------------------------------+------------------------+-------+
```
